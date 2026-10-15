#!/usr/bin/env python3
"""Claw CPU Control. Hardware writes are scaling_max_freq and intel_pstate/no_turbo.
Production uses fixed paths. Hardware is injectable ONLY through the Python test API.
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import pwd
import signal
import socket
import struct
import subprocess
import time
import uuid

LOG = logging.getLogger('claw-cpu-control')
STATE = Path('/var/lib/claw-cpu-control')
SOCKET = '/run/claw-cpu-control/control.sock'
MAX_MESSAGE = 16384
PRESETS = ('default', 'balanced', 'powersave')
WRITE_TIMEOUT = 1.0
WRITE_POLL_INTERVAL = 0.01


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def cpu_set(value):
    out = set()
    for item in value.replace(' ', ',').split(','):
        if not item:
            continue
        bounds = item.split('-')
        lo, hi = int(bounds[0]), int(bounds[-1])
        if lo < 0 or hi < lo or hi > 4095 or len(bounds) > 2:
            raise ValueError('Invalid CPU list')
        out.update(range(lo, hi + 1))
    return out


class Hardware:
    def __init__(self):
        self.root = Path('/sys/devices/system/cpu')

    def read(self, path):
        return path.read_text().strip()

    def discover(self):
        if self.read(Path('/sys/class/dmi/id/board_name')) != 'MS-1T91':
            raise ValueError('仅支持已核查的 MS-1T91 主板')
        groups = {key: cpu_set(self.read(Path('/sys/bus/event_source/devices') / pmu / 'cpus'))
                  for key, pmu in [('P', 'cpu_core'), ('E', 'cpu_atom')]}
        online = cpu_set(self.read(self.root / 'online'))
        if len(groups['P']) != 2 or len(groups['E']) != 8 or groups['P'] & groups['E'] or online != groups['P'] | groups['E']:
            raise ValueError('核心拓扑与已核查的 2P+8E 不同；请重新检查 BIOS/在线核心')
        pstate = {n: self.read(self.root / 'intel_pstate' / n)
                  for n in ['status', 'no_turbo', 'max_perf_pct', 'min_perf_pct', 'hwp_dynamic_boost']}
        if pstate['status'] != 'active':
            raise ValueError('需要 intel_pstate active 模式')
        policies = {}
        seen = set()
        for p in sorted((self.root / 'cpufreq').glob('policy[0-9]*')):
            cpus = cpu_set(self.read(p / 'related_cpus'))
            affected = cpu_set(self.read(p / 'affected_cpus'))
            group = next((g for g, members in groups.items() if cpus and cpus <= members), None)
            if not group or cpus & seen or cpus != affected or len(cpus) != 1:
                raise ValueError('不支持共享、离线或跨核心类型的 policy')
            if self.read(p / 'scaling_driver') != 'intel_pstate':
                raise ValueError('CPU 驱动已变化')
            resolved = p.resolve()
            if not resolved.is_relative_to(self.root):
                raise ValueError('policy 路径不在 CPU sysfs 中')
            seen |= cpus
            policies[p.name] = {'group': group, 'cpus': sorted(cpus), 'path': str(resolved),
                'min': int(self.read(p / 'scaling_min_freq')),
                'max': int(self.read(p / 'scaling_max_freq')),
                'hw_min': int(self.read(p / 'cpuinfo_min_freq')),
                'hw_max': int(self.read(p / 'cpuinfo_max_freq')),
                'base': int(self.read(p / 'base_frequency')),
                'governor': self.read(p / 'scaling_governor'),
                'epp': self.read(p / 'energy_performance_preference')}
        if seen != online or len(policies) != 10:
            raise ValueError('CPU policy 集合不完整')
        return {'policies': policies, 'global': pstate}

    def write_no_turbo(self, value):
        if value not in ('0', '1'):
            raise ValueError('Invalid no_turbo value')
        (self.root / 'intel_pstate/no_turbo').write_text(value)

    def write_max(self, name, value):
        if not name.startswith('policy') or not name[6:].isdigit():
            raise ValueError('Invalid policy name')
        # No caller-controlled sysfs paths. No writes to min/governor/EPP/TDP.
        with open(self.root / 'cpufreq' / name / 'scaling_max_freq', 'w') as f:
            f.write(str(value))


def fingerprint(snapshot):
    return {n: {k: p[k] for k in ['group', 'cpus', 'path', 'hw_min', 'hw_max']}
            for n, p in snapshot['policies'].items()}


def guards(snapshot):
    return {'global': snapshot['global'],
            'policies': {n: {k: p[k] for k in ['min', 'governor', 'epp']}
                         for n, p in snapshot['policies'].items()}}


class Engine:
    def __init__(self, hardware, directory, boot_id, gaming):
        self.hw, self.directory, self.boot, self.gaming = hardware, Path(directory), boot_id, gaming
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory / 'transaction.json'
        self.presets_file = self.directory / 'presets.json'
        self.presets = {k: None for k in PRESETS}
        self.ui = {'name': 'default', 'independent': False}
        if self.presets_file.exists():
            stored = json.loads(self.presets_file.read_text())
            if stored.get('schema') != 1:
                raise ValueError('未知的预设文件版本')
            ui = stored.get('ui', self.ui)
            self.validate_ui(ui)
            self.ui = ui
            for name in PRESETS:
                value = stored['presets'].get(name)
                if value is not None:
                    self.check_numbers(value)
                self.presets[name] = value
        self.tx = None
        self.state = 'idle'
        self.message = ''
        self.revision = uuid.uuid4().hex
        self.last_heartbeat = 0.0
        self.owner = None
        self.next_recovery = 0.0
        self.cache = {}
        if self.journal.exists():
            self.tx = json.loads(self.journal.read_text())
            if self.tx.get('schema') != 1:
                raise ValueError('未知的恢复记录版本，拒绝覆盖')
            if self.tx['boot'] != self.boot:
                self.archive('previous-boot')
            else:
                self.state = 'recovery_required'
                # Resolve write-ahead record, never apply a cap on recovery.
                try:
                    snap = self.hw.discover()
                    pending = self.tx.get('pending')
                    if pending and fingerprint(snap) == self.tx['topology']:
                        n = pending['policy']
                        current = snap['policies'][n]['max']
                        if current == pending['target']:
                            self.tx['last'][n] = current
                        elif current == pending['previous']:
                            self.tx['last'][n] = current
                    self.restore()
                except Exception as e:
                    self.message = '恢复未完成：' + str(e)
                    LOG.exception('Startup recovery failed')

    def changed(self):
        self.revision = uuid.uuid4().hex

    def persist(self):
        atomic_json(self.journal, self.tx)

    def archive(self, reason):
        if self.journal.exists():
            archive = self.directory / ('history-' + str(time.time_ns()) + '-' + reason + '.json')
            os.replace(self.journal, archive)
            fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.tx = None
        self.owner = None
        self.state = 'idle'
        self.changed()

    @staticmethod
    def validate_ui(ui):
        if type(ui) is not dict or set(ui) != {'name', 'independent'} or ui['name'] not in PRESETS or type(ui['independent']) is not bool:
            raise ValueError('无效界面偏好')

    def save_settings(self, presets, ui):
        self.validate_ui(ui)
        atomic_json(self.presets_file, {'schema': 1, 'presets': presets, 'ui': ui})
        self.presets, self.ui = presets, ui
        self.changed()

    @staticmethod
    def check_numbers(values):
        if set(values) != {'p_max_khz', 'e_max_khz'}:
            raise ValueError('需要 P/E 上限，禁止额外字段')
        for v in values.values():
            if type(v) is not int or not 100000 <= v <= 10000000:
                raise ValueError('频率必须为整数 kHz')

    def target_values(self, snapshot, values):
        self.check_numbers(values)
        targets = {}
        for n, p in snapshot['policies'].items():
            requested = values['p_max_khz' if p['group'] == 'P' else 'e_max_khz']
            target = min(requested, p['hw_max'], p.get('base', p['hw_max']) if snapshot['global']['no_turbo'] == '1' else p['hw_max'])
            if target < max(p['min'], p['hw_min']):
                raise ValueError(n + ' 目标低于当前最小频率；未修改任何字段')
            targets[n] = target
        for group, key in [('P', 'p_max_khz'), ('E', 'e_max_khz')]:
            if values[key] > max(self.tx.get('ceilings', {}).get(n, p['hw_max']) if self.tx else p['hw_max'] for n,p in snapshot['policies'].items() if p['group'] == group):
                raise ValueError(group + ' 组请求超出硬件范围')
        return targets

    def owned(self, snap):
        return self.tx and fingerprint(snap) == self.tx['topology'] and all(
            snap['policies'][n]['max'] == v for n, v in self.tx['last'].items())

    def monitor(self):
        if not self.tx or self.state not in ('active', 'conflict'):
            return
        try:
            snap = self.hw.discover()
            reason = None
            if not self.owned(snap) or guards(snap) != self.tx['guards']:
                reason = '检测到外部配置或拓扑变化，已暂停应用'
            if reason and self.state != 'conflict':
                self.state, self.message = 'conflict', reason
                self.changed()
        except Exception as e:
            if self.state != 'conflict':
                self.state, self.message = 'conflict', str(e)
                self.changed()

    def watchdog(self, now):
        self.monitor()
        if self.tx and now >= self.next_recovery and (now - self.last_heartbeat > 60 or not self.gaming()):
            self.next_recovery = now + 30
            self.restore()

    def status(self):
        self.monitor()
        try:
            snap = self.hw.discover()
            error = None
        except Exception as e:
            snap, error = {'policies': {}, 'global': {}}, str(e)
        return {'state': self.state if not error else 'read_only', 'revision': self.revision,
                'message': error or self.message, 'gaming': bool(self.gaming()),
                'policies': {n: {**p, 'request_max': self.tx.get('ceilings', {}).get(n, p['hw_max']) if self.tx else p['hw_max']} for n,p in snap['policies'].items()}, 'global': snap['global'], 'presets': self.presets, 'ui': self.ui,
                'applied': self.tx.get('applied') if self.tx else None,
                'turbo_enabled': snap['global'].get('no_turbo') == '0' if not error else None,
                'has_transaction': self.tx is not None,
                'baseline': self.tx.get('baseline') if self.tx else None}

    def takeover(self, snap):
        limits = {n: p['max'] for n, p in snap['policies'].items()}
        self.tx = {'schema': 1, 'boot': self.boot, 'topology': fingerprint(snap),
                   'guards': guards(snap), 'baseline': limits.copy(), 'last': limits.copy(),
                   'pending': None, 'applied': None, 'ceilings': {n:p['hw_max'] for n,p in snap['policies'].items()}}
        self.persist()

    def accept_turbo_transition(self, before, after, target):
        # This CachyOS kernel changes cpuinfo_max_freq on no_turbo writes.
        # Core identity and minimum capability must remain unchanged.
        identity = lambda snap: {n:{k:p[k] for k in ('group','cpus','path','hw_min')} for n,p in snap['policies'].items()}
        if identity(after) != identity(before):
            raise ValueError('睿频切换期间拓扑变化')
        expected_global = {**before['global'], 'no_turbo': target}
        if after['global'] != expected_global:
            raise ValueError('睿频切换期间其他全局设置变化')
        for n, p in after['policies'].items():
            old = before['policies'][n]
            if (p['governor'], p['epp'], p['min']) != (old['governor'], old['epp'], old['min']):
                raise ValueError('睿频切换期间 governor/EPP/最小频率变化')
            if target == '1':
                if p['hw_max'] not in (old['hw_max'], p['base']):
                    raise ValueError(n + ' 关闭睿频后的硬件上限变化异常')
                expected = min(old['max'], p['base'])
                if p['max'] != expected:
                    raise ValueError(n + ' 关闭睿频后的回读不符合预期')
            elif not (old['max'] <= p['max'] <= p['hw_max'] and old['hw_max'] <= p['hw_max'] <= 10000000):
                raise ValueError(n + ' 开启睿频后的回读不符合预期')
        self.tx['topology'] = fingerprint(after)
        ceilings = self.tx.setdefault('ceilings', {n:p['hw_max'] for n,p in before['policies'].items()})
        for n,p in after['policies'].items():
            ceilings[n] = max(ceilings[n], p['hw_max'])
        self.tx['last'] = {n: p['max'] for n,p in after['policies'].items()}
        self.tx['guards'] = guards(after)
        self.tx['turbo']['last'] = target
        self.tx['turbo']['pending'] = None
        self.persist()

    def write_turbo(self, target, snap):
        turbo = self.tx['turbo']
        if snap['global']['no_turbo'] != turbo['last'] or not self.owned(snap) or guards(snap) != self.tx['guards']:
            raise ValueError('配置已被其他组件修改，拒绝切换睿频')
        if target == '1' and any(p['min'] > p['base'] for p in snap['policies'].values()):
            raise ValueError('当前最小频率高于基准频率，不能安全关闭睿频')
        turbo['pending'] = {'target': target, 'before': snap}
        self.persist()
        self.hw.write_no_turbo(target)
        deadline = time.monotonic() + WRITE_TIMEOUT
        # no_turbo updates policy limits too. Allow that update to settle.
        while True:
            after = self.hw.discover()
            try:
                if after['global']['no_turbo'] != target:
                    raise ValueError('睿频状态尚未更新')
                self.accept_turbo_transition(snap, after, target)
                return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(WRITE_POLL_INTERVAL)

    def set_turbo(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('睿频开关必须为布尔值')
        if not self.gaming() or time.monotonic() - self.last_heartbeat > 60:
            raise ValueError('仅在游戏模式和插件心跳正常时切换睿频')
        if self.state not in ('idle', 'active'):
            raise ValueError('请先恢复或解决冲突')
        snap = self.hw.discover()
        target = '0' if enabled else '1'
        if target == snap['global']['no_turbo']:
            return
        if self.tx and (not self.owned(snap) or guards(snap) != self.tx['guards']):
            raise ValueError('配置已变化，请先恢复')
        if not self.tx:
            self.takeover(snap)
        if 'turbo' not in self.tx:
            self.tx['turbo'] = {'baseline': snap['global']['no_turbo'], 'last': snap['global']['no_turbo'], 'pending': None}
            self.persist()
        try:
            self.write_turbo(target, snap)
            self.state = 'active'
            if enabled and self.tx.get('applied'):
                applied = self.tx['applied']
                self.apply(applied['name'], {k: applied[k] for k in ('p_max_khz', 'e_max_khz')})
            self.message = 'CPU 睿频已' + ('开启' if enabled else '关闭')
            self.changed()
        except Exception:
            self.state, self.message = 'recovery_required', '睿频切换未完成，保留恢复记录；请恢复系统原始配置'
            self.changed()
            raise

    def recover_turbo_pending(self):
        turbo = self.tx.get('turbo')
        if not turbo or not turbo.get('pending'):
            return
        pending = turbo['pending']
        snap = self.hw.discover()
        if snap['global']['no_turbo'] == pending['target']:
            self.accept_turbo_transition(pending['before'], snap, pending['target'])
        elif snap == pending['before']:
            turbo['pending'] = None
            self.persist()
        else:
            raise ValueError('睿频切换结果不确定，保留恢复记录')

    def write_owned(self, name, target, snap):
        if fingerprint(snap) != self.tx['topology']:
            raise ValueError('拓扑已变化，拒绝写入')
        p = snap['policies'][name]
        if p['max'] != self.tx['last'][name]:
            raise ValueError(name + ' 已被其他组件接管')
        if not p['min'] <= target <= p['hw_max']:
            raise ValueError(name + ' 恢复/设置目标不在当前范围')
        self.tx['pending'] = {'policy': name, 'previous': p['max'], 'target': target}
        self.persist()  # durable before sysfs write
        self.hw.write_max(name, target)
        # cpufreq's QoS update is asynchronous: an immediate read may still
        # return the previous limit. Never record that old value as our result.
        deadline = time.monotonic() + WRITE_TIMEOUT
        while True:
            observed = self.hw.discover()
            if fingerprint(observed) != self.tx['topology']:
                raise ValueError('写入期间拓扑变化')
            actual = observed['policies'][name]['max']
            if actual == target:
                break
            if actual != p['max']:
                raise ValueError(f'{name} 回读 {actual} 与目标 {target} 不一致')
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{name} 等待目标 {target} 超时，回读仍为 {actual}')
            time.sleep(WRITE_POLL_INTERVAL)
        self.tx['last'][name] = actual
        self.tx['pending'] = None
        self.persist()
        return actual

    def apply(self, name, values):
        if name not in PRESETS:
            raise ValueError('无效预设')
        if self.state not in ('idle', 'active'):
            raise ValueError('请先恢复或解决冲突')
        if not self.gaming():
            raise ValueError('仅允许在已确认的 gamescope 游戏会话中应用')
        snap = self.hw.discover()
        targets = self.target_values(snap, values)
        if self.tx and (not self.owned(snap) or guards(snap) != self.tx['guards']):
            raise ValueError('配置已变化，请先恢复')
        fresh = self.tx is None
        previous = {n: p['max'] for n, p in snap['policies'].items()}
        previous_applied = self.tx.get('applied') if self.tx else None
        if fresh:
            self.takeover(snap)
        self.state = 'applying'
        touched = []
        try:
            for n, target in targets.items():
                now = self.hw.discover()
                if guards(now) != self.tx['guards'] or not self.owned(now):
                    raise ValueError('应用期间外部配置发生变化')
                if target != now['policies'][n]['max']:
                    touched.append(n)
                    self.write_owned(n, target, now)
            self.tx['applied'] = {'name': name, **values}
            self.persist()
            self.state, self.message = 'active', '已应用；下方显示真实回读上限'
            self.changed()
            LOG.info('Applied %s %s', name, values)
        except Exception:
            LOG.exception('CPU apply failed; compensating writes')
            failures = []
            # Resolve a potentially completed write whose read/persist failed.
            for n in reversed(touched):
                try:
                    now = self.hw.discover()
                    pending = self.tx.get('pending')
                    if pending and pending['policy'] == n and now['policies'][n]['max'] == pending['target']:
                        self.tx['last'][n] = pending['target']
                    if now['policies'][n]['max'] != previous[n] or (pending and pending['policy'] == n):
                        self.write_owned(n, previous[n], now)
                except Exception as e:
                    failures.append(n + ': ' + str(e))
            if not failures:
                self.tx['pending'] = None
            self.tx['applied'] = previous_applied
            self.persist()
            if failures:
                self.state, self.message = 'recovery_required', '; '.join(failures)
            elif fresh:
                self.archive('apply-failed-rolled-back')
                self.message = '应用失败，已回退到原始上限'
            else:
                self.state, self.message = 'active', '应用失败，已回退到上一次成功状态'
            self.changed()
            raise

    def restore(self):
        if not self.tx:
            return
        self.recover_turbo_pending()
        snap = self.hw.discover()
        turbo = self.tx.get('turbo')
        if turbo and snap['global']['no_turbo'] != turbo['last']:
            self.archive('external-turbo-owner')
            self.message = '外部程序已修改睿频，已释放控制并保留当前设置'
            return
        if turbo and turbo['last'] != turbo['baseline']:
            self.write_turbo(turbo['baseline'], snap)
            snap = self.hw.discover()
        if fingerprint(snap) != self.tx['topology']:
            self.state = 'recovery_required'
            raise ValueError('拓扑改变，保留恢复记录；未写入旧值')
        self.state = 'restoring'
        skipped, failed = [], []
        for n, baseline in self.tx['baseline'].items():
            try:
                now = self.hw.discover()
                current = now['policies'][n]['max']
                if current == baseline:
                    continue
                if current != self.tx['last'][n]:
                    skipped.append(n)
                    continue
                self.write_owned(n, baseline, now)
                if self.tx['last'][n] != baseline:
                    raise ValueError('驱动没有精确接受原始值')
            except Exception as e:
                failed.append(n + ': ' + str(e))
        if failed:
            self.state, self.message = 'recovery_required', '恢复未完成：' + '; '.join(failed)
            self.persist()
            self.changed()
            raise ValueError(self.message)
        self.archive('restored' if not skipped else 'external-owner')
        self.message = '已恢复原始上限' if not skipped else '已释放控制；保留外部修改：' + ', '.join(skipped)
        LOG.info(self.message)

    def request(self, req, uid):
        allowed = {'get_status': set(), 'heartbeat': {'session_token'},
                   'save_preset': {'name', 'p_max_khz', 'e_max_khz'},
                   'apply': {'name', 'p_max_khz', 'e_max_khz'}, 'restore': set(),
                   'configure': {'name', 'p_max_khz', 'e_max_khz', 'independent'},
                   'save_ui': {'name', 'independent'}, 'set_turbo': {'enabled'}}
        if type(req) is not dict:
            raise ValueError('请求必须是对象')
        method = req.get('method')
        if method not in allowed:
            raise ValueError('未知方法')
        common = {'method'} if method in ('get_status', 'heartbeat') else {'method', 'request_id', 'expected_revision'}
        if set(req) != common | allowed[method]:
            raise ValueError('请求字段不匹配')
        if method == 'heartbeat':
            token = req['session_token']
            if not isinstance(token, str) or not 16 <= len(token) <= 128:
                raise ValueError('无效会话标识')
            self.last_heartbeat = time.monotonic()
            return {'ok': True}
        if method == 'get_status':
            return {'ok': True, **self.status()}
        rid = req['request_id']
        if not isinstance(rid, str) or not 8 <= len(rid) <= 128:
            raise ValueError('无效请求编号')
        digest = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
        key = (uid, rid)
        if key in self.cache:
            prior_digest, result = self.cache[key]
            if prior_digest != digest:
                raise ValueError('同一请求编号不能复用于不同操作')
            return result
        if req['expected_revision'] != self.revision:
            raise ValueError('状态已变化，请刷新后重试')
        try:
            if method == 'set_turbo':
                self.set_turbo(req['enabled'])
            elif method == 'save_ui':
                self.save_settings(self.presets, {k: req[k] for k in ('name', 'independent')})
            elif method in ('save_preset', 'configure'):
                name = req['name']
                if name not in PRESETS:
                    raise ValueError('无效预设')
                values = {k: req[k] for k in ('p_max_khz', 'e_max_khz')}
                self.target_values(self.hw.discover(), values)
                new_presets = {**self.presets, name: values}
                ui = {k: req[k] for k in ('name', 'independent')} if method == 'configure' else self.ui
                self.save_settings(new_presets, ui)
                self.message = '已保存配置；尚未应用'
                if method == 'configure':
                    if self.gaming():
                        if time.monotonic() - self.last_heartbeat > 60:
                            raise ValueError('配置已保存，但插件后端心跳不可用，未应用')
                        self.apply(name, values)
                    else:
                        self.message = '已保存；当前不是游戏模式，未应用。进入游戏模式后主动选择预设或调整才生效。'
            elif method == 'apply':
                if time.monotonic() - self.last_heartbeat > 60:
                    raise ValueError('插件后端心跳不可用')
                self.apply(req['name'], {k: req[k] for k in ('p_max_khz', 'e_max_khz')})
            else:
                self.restore()
            result = {'ok': True, **self.status()}
        except Exception as e:
            result = {'ok': False, **self.status(), 'error': str(e)}
        self.cache[key] = (digest, result)
        if len(self.cache) > 128:
            del self.cache[next(iter(self.cache))]
        return result


def game_session(uid):
    """Fail closed. Verify logind and a same-UID Steam process in gamescope."""
    try:
        sessions = subprocess.check_output(['loginctl', 'list-sessions', '--no-legend', '--no-pager'], text=True, timeout=2)
        active = False
        for line in sessions.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1] != str(uid):
                continue
            props = subprocess.check_output(['loginctl', 'show-session', parts[0],
                '-p', 'Active', '-p', 'Remote', '-p', 'Desktop', '-p', 'Type'], text=True, timeout=2)
            info = dict(x.split('=', 1) for x in props.splitlines() if '=' in x)
            if info.get('Active') == 'yes' and info.get('Remote') == 'no' and 'gamescope' in info.get('Desktop', '').lower():
                active = True
        if not active:
            return False
        for p in Path('/proc').glob('[0-9]*'):
            try:
                if p.stat().st_uid != uid or (p / 'comm').read_text().strip() != 'steam':
                    continue
                env = (p / 'environ').read_bytes().decode(errors='replace').split('\0')
                if any(x in env for x in ['XDG_CURRENT_DESKTOP=gamescope', 'XDG_SESSION_DESKTOP=gamescope']):
                    return True
            except OSError:
                continue
    except (OSError, subprocess.SubprocessError):
        pass
    return False


async def serve(uid):
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / 'daemon.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    engine = Engine(Hardware(), STATE, Path('/proc/sys/kernel/random/boot_id').read_text().strip(), lambda: game_session(uid))
    from fan import FanEngine, FanHardware
    fans = FanEngine(FanHardware(), STATE, atomic_json, engine.gaming, lambda: engine.last_heartbeat, engine.boot)
    serial = asyncio.Lock()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def client(reader, writer):
        try:
            peer = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, peer_uid, _ = struct.unpack('3i', peer)
            if peer_uid not in (uid, 0):
                raise PermissionError('该用户无权访问控制服务')
            data = await asyncio.wait_for(reader.readline(), 3)
            if not data.endswith(b'\n') or len(data) > MAX_MESSAGE:
                raise ValueError('请求过长或未完整结束')
            request = json.loads(data)
            async with serial:
                result = fans.request(request) if isinstance(request, dict) and str(request.get('method', '')).startswith('fan_') else engine.request(request, peer_uid)
        except Exception as e:
            result = {'ok': False, 'error': str(e)}
        try:
            writer.write((json.dumps(result, ensure_ascii=False) + '\n').encode())
            await asyncio.wait_for(writer.drain(), 3)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def monitor():
        last_mono, last_boot = time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)
        while not stop.is_set():
            await asyncio.sleep(2)
            async with serial:
                now, boot = time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)
                if (boot - last_boot) - (now - last_mono) > 3:
                    engine.last_heartbeat = now  # post-resume reconnect grace, no reapply
                last_mono, last_boot = now, boot
                try:
                    fans.watchdog()
                    engine.watchdog(now)
                except Exception:
                    LOG.exception('Automatic recovery incomplete; retry after 30 seconds')

    Path(SOCKET).parent.mkdir(parents=True, exist_ok=True)
    Path(SOCKET).unlink(missing_ok=True)
    server = await asyncio.start_unix_server(client, SOCKET, limit=MAX_MESSAGE)
    os.chown(SOCKET, uid, -1)
    os.chmod(SOCKET, 0o600)
    task = asyncio.create_task(monitor())
    LOG.info('Started read-only idle; no boot preset application')
    await stop.wait()
    server.close()
    await server.wait_closed()
    task.cancel()
    async with serial:
        try:
            fans.restore()
        finally:
            if engine.tx:
                engine.restore()
    Path(SOCKET).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--user', default='deck')
    parser.add_argument('--inspect', action='store_true', help='Read-only hardware probe; no daemon/state writes')
    args = parser.parse_args()
    if args.inspect:
        print(json.dumps(Hardware().discover(), ensure_ascii=False, indent=2))
        return
    if os.geteuid() != 0:
        parser.error('守护进程需要root；只读探测使用 --inspect')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    asyncio.run(serve(pwd.getpwnam(args.user).pw_uid))


if __name__ == '__main__':
    main()
