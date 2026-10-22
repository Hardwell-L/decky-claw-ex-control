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
            raise ValueError('Only the verified MS-1T91 board is supported')
        groups = {key: cpu_set(self.read(Path('/sys/bus/event_source/devices') / pmu / 'cpus'))
                  for key, pmu in [('P', 'cpu_core'), ('E', 'cpu_atom')]}
        online = cpu_set(self.read(self.root / 'online'))
        if len(groups['P']) != 2 or len(groups['E']) != 8 or groups['P'] & groups['E'] or online != groups['P'] | groups['E']:
            raise ValueError('Topology differs from the verified 2P+8E layout; check BIOS and online cores')
        pstate = {n: self.read(self.root / 'intel_pstate' / n)
                  for n in ['status', 'no_turbo', 'max_perf_pct', 'min_perf_pct', 'hwp_dynamic_boost']}
        if pstate['status'] != 'active':
            raise ValueError('intel_pstate active mode is required')
        policies = {}
        seen = set()
        for p in sorted((self.root / 'cpufreq').glob('policy[0-9]*')):
            cpus = cpu_set(self.read(p / 'related_cpus'))
            affected = cpu_set(self.read(p / 'affected_cpus'))
            group = next((g for g, members in groups.items() if cpus and cpus <= members), None)
            if not group or cpus & seen or cpus != affected or len(cpus) != 1:
                raise ValueError('Shared, offline or mixed-core-type policies are unsupported')
            if self.read(p / 'scaling_driver') != 'intel_pstate':
                raise ValueError('CPU driver changed')
            resolved = p.resolve()
            if not resolved.is_relative_to(self.root):
                raise ValueError('Policy path is outside CPU sysfs')
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
            raise ValueError('CPU policy set is incomplete')
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


def without_epp(snapshot):
    return {**snapshot, 'policies': {n: {k:v for k,v in p.items() if k != 'epp'}
                                  for n,p in snapshot['policies'].items()}}

def guards(snapshot):
    return {'global': snapshot['global'],
            'policies': {n: {k: p[k] for k in ['min', 'governor']}
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
                raise ValueError('Unknown preset file version')
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
            # Legacy journals included EPP, which is owned by the system profile.
            for policy in self.tx.get('guards', {}).get('policies', {}).values():
                policy.pop('epp', None)
            if self.tx.get('schema') != 1:
                raise ValueError('Unknown recovery record version; refusing overwrite')
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
                    self.message = 'Recovery incomplete: ' + str(e)
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
            raise ValueError('Invalid UI preferences')

    def save_settings(self, presets, ui):
        self.validate_ui(ui)
        atomic_json(self.presets_file, {'schema': 1, 'presets': presets, 'ui': ui})
        self.presets, self.ui = presets, ui
        self.changed()

    @staticmethod
    def check_numbers(values):
        if set(values) != {'p_max_khz', 'e_max_khz'}:
            raise ValueError('P/E limits required; extra fields are not allowed')
        for v in values.values():
            if type(v) is not int or not 100000 <= v <= 10000000:
                raise ValueError('Frequency must be an integer in kHz')

    def target_values(self, snapshot, values):
        self.check_numbers(values)
        targets = {}
        for n, p in snapshot['policies'].items():
            requested = values['p_max_khz' if p['group'] == 'P' else 'e_max_khz']
            target = min(requested, p['hw_max'], p.get('base', p['hw_max']) if snapshot['global']['no_turbo'] == '1' else p['hw_max'])
            if target < max(p['min'], p['hw_min']):
                raise ValueError(n + ' target is below the current minimum; nothing changed')
            targets[n] = target
        for group, key in [('P', 'p_max_khz'), ('E', 'e_max_khz')]:
            if values[key] > max(self.tx.get('ceilings', {}).get(n, p['hw_max']) if self.tx else p['hw_max'] for n,p in snapshot['policies'].items() if p['group'] == group):
                raise ValueError(group + ' group request is outside hardware limits')
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
                reason = 'External settings or topology changed; application paused'
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
            raise ValueError('Topology changed during Turbo toggle')
        expected_global = {**before['global'], 'no_turbo': target}
        if after['global'] != expected_global:
            raise ValueError('Other global settings changed during Turbo toggle')
        for n, p in after['policies'].items():
            old = before['policies'][n]
            if (p['governor'], p['min']) != (old['governor'], old['min']):
                raise ValueError('Governor or minimum frequency changed during Turbo toggle')
            if target == '1':
                if p['hw_max'] not in (old['hw_max'], p['base']):
                    raise ValueError(n + ' unexpected hardware limit after disabling Turbo')
                expected = min(old['max'], p['base'])
                if p['max'] != expected:
                    raise ValueError(n + ' unexpected readback after disabling Turbo')
            elif not (old['max'] <= p['max'] <= p['hw_max'] and old['hw_max'] <= p['hw_max'] <= 10000000):
                raise ValueError(n + ' unexpected readback after enabling Turbo')
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
            raise ValueError('Settings changed externally; refusing Turbo toggle')
        if target == '1' and any(p['min'] > p['base'] for p in snap['policies'].values()):
            raise ValueError('Minimum frequency exceeds base frequency; cannot safely disable Turbo')
        turbo['pending'] = {'target': target, 'before': snap}
        self.persist()
        self.hw.write_no_turbo(target)
        deadline = time.monotonic() + WRITE_TIMEOUT
        # no_turbo updates policy limits too. Allow that update to settle.
        while True:
            after = self.hw.discover()
            try:
                if after['global']['no_turbo'] != target:
                    raise ValueError('Turbo state has not updated yet')
                self.accept_turbo_transition(snap, after, target)
                return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(WRITE_POLL_INTERVAL)

    def set_turbo(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Turbo toggle must be a boolean')
        if not self.gaming() or time.monotonic() - self.last_heartbeat > 60:
            raise ValueError('Turbo toggle requires game mode and a live heartbeat')
        if self.state not in ('idle', 'active'):
            raise ValueError('Restore settings or resolve the conflict first')
        snap = self.hw.discover()
        target = '0' if enabled else '1'
        if target == snap['global']['no_turbo']:
            return
        if self.tx and (not self.owned(snap) or guards(snap) != self.tx['guards']):
            raise ValueError('Settings changed; restore first')
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
            self.message = 'CPU Turbo ' + ('enabled' if enabled else 'disabled')
            self.changed()
        except Exception:
            self.state, self.message = 'recovery_required', 'Turbo toggle incomplete; recovery record retained. Restore original settings'
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
        elif without_epp(snap) == without_epp(pending['before']):
            turbo['pending'] = None
            self.persist()
        else:
            raise ValueError('Turbo toggle outcome is uncertain; recovery record retained')

    def write_owned(self, name, target, snap):
        if fingerprint(snap) != self.tx['topology']:
            raise ValueError('Topology changed; refusing write')
        p = snap['policies'][name]
        if p['max'] != self.tx['last'][name]:
            raise ValueError(name + ' is controlled by another component')
        if not p['min'] <= target <= p['hw_max']:
            raise ValueError(name + ' restore/set target is outside the current range')
        self.tx['pending'] = {'policy': name, 'previous': p['max'], 'target': target}
        self.persist()  # durable before sysfs write
        self.hw.write_max(name, target)
        # cpufreq's QoS update is asynchronous: an immediate read may still
        # return the previous limit. Never record that old value as our result.
        deadline = time.monotonic() + WRITE_TIMEOUT
        while True:
            observed = self.hw.discover()
            if fingerprint(observed) != self.tx['topology']:
                raise ValueError('Topology changed during write')
            actual = observed['policies'][name]['max']
            if actual == target:
                break
            if actual != p['max']:
                raise ValueError(f'{name} readback {actual} differs from target {target}')
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{name} timed out waiting for {target}; readback is {actual}')
            time.sleep(WRITE_POLL_INTERVAL)
        self.tx['last'][name] = actual
        self.tx['pending'] = None
        self.persist()
        return actual

    def apply(self, name, values):
        if name not in PRESETS:
            raise ValueError('Invalid preset')
        if self.state not in ('idle', 'active'):
            raise ValueError('Restore settings or resolve the conflict first')
        if not self.gaming():
            raise ValueError('Application requires a verified gamescope gaming session')
        snap = self.hw.discover()
        targets = self.target_values(snap, values)
        if self.tx and (not self.owned(snap) or guards(snap) != self.tx['guards']):
            raise ValueError('Settings changed; restore first')
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
                    raise ValueError('External settings changed during application')
                if target != now['policies'][n]['max']:
                    touched.append(n)
                    self.write_owned(n, target, now)
            self.tx['applied'] = {'name': name, **values}
            self.persist()
            self.state, self.message = 'active', 'Applied; actual limits are shown below'
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
                self.message = 'Application failed; original limits restored'
            else:
                self.state, self.message = 'active', 'Application failed; previous successful state restored'
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
            self.message = 'Turbo changed externally; released control and retained current settings'
            return
        if turbo and turbo['last'] != turbo['baseline']:
            self.write_turbo(turbo['baseline'], snap)
            snap = self.hw.discover()
        if fingerprint(snap) != self.tx['topology']:
            self.state = 'recovery_required'
            raise ValueError('Topology changed; recovery record retained, old values not written')
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
                    raise ValueError('Driver did not accept the exact original value')
            except Exception as e:
                failed.append(n + ': ' + str(e))
        if failed:
            self.state, self.message = 'recovery_required', 'Recovery incomplete: ' + '; '.join(failed)
            self.persist()
            self.changed()
            raise ValueError(self.message)
        self.archive('restored' if not skipped else 'external-owner')
        self.message = 'Original limits restored' if not skipped else 'Control released; external changes retained: ' + ', '.join(skipped)
        LOG.info(self.message)

    def request(self, req, uid):
        allowed = {'get_status': set(), 'heartbeat': {'session_token'},
                   'save_preset': {'name', 'p_max_khz', 'e_max_khz'},
                   'apply': {'name', 'p_max_khz', 'e_max_khz'}, 'restore': set(),
                   'configure': {'name', 'p_max_khz', 'e_max_khz', 'independent'},
                   'save_ui': {'name', 'independent'}, 'set_turbo': {'enabled'}}
        if type(req) is not dict:
            raise ValueError('Request must be an object')
        method = req.get('method')
        if method not in allowed:
            raise ValueError('Unknown method')
        common = {'method'} if method in ('get_status', 'heartbeat') else {'method', 'request_id', 'expected_revision'}
        if set(req) != common | allowed[method]:
            raise ValueError('Invalid request fields')
        if method == 'heartbeat':
            token = req['session_token']
            if not isinstance(token, str) or not 16 <= len(token) <= 128:
                raise ValueError('Invalid session token')
            self.last_heartbeat = time.monotonic()
            return {'ok': True}
        if method == 'get_status':
            return {'ok': True, **self.status()}
        rid = req['request_id']
        if not isinstance(rid, str) or not 8 <= len(rid) <= 128:
            raise ValueError('Invalid request ID')
        digest = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
        key = (uid, rid)
        if key in self.cache:
            prior_digest, result = self.cache[key]
            if prior_digest != digest:
                raise ValueError('Request ID cannot be reused for a different operation')
            return result
        if req['expected_revision'] != self.revision:
            raise ValueError('State changed; refresh and retry')
        try:
            if method == 'set_turbo':
                self.set_turbo(req['enabled'])
            elif method == 'save_ui':
                self.save_settings(self.presets, {k: req[k] for k in ('name', 'independent')})
            elif method in ('save_preset', 'configure'):
                name = req['name']
                if name not in PRESETS:
                    raise ValueError('Invalid preset')
                values = {k: req[k] for k in ('p_max_khz', 'e_max_khz')}
                self.target_values(self.hw.discover(), values)
                new_presets = {**self.presets, name: values}
                ui = {k: req[k] for k in ('name', 'independent')} if method == 'configure' else self.ui
                self.save_settings(new_presets, ui)
                self.message = 'Settings saved; not applied'
                if method == 'configure':
                    if self.gaming():
                        if time.monotonic() - self.last_heartbeat > 60:
                            raise ValueError('Settings saved but not applied: plugin heartbeat unavailable')
                        self.apply(name, values)
                    else:
                        self.message = 'Saved, not applied outside game mode. Select a preset or adjust settings in game mode to apply.'
            elif method == 'apply':
                if time.monotonic() - self.last_heartbeat > 60:
                    raise ValueError('Plugin heartbeat unavailable')
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


class SessionProbe:
    """Share a short-lived session observation; invalidate before mutations/resume."""
    def __init__(self, probe, clock=time.monotonic, ttl=1.0):
        self.probe, self.clock, self.ttl = probe, clock, ttl
        self.expires = float('-inf')
        self.value = False
    def invalidate(self):
        self.expires = float('-inf')
    def __call__(self):
        now = self.clock()
        if now >= self.expires:
            self.value = bool(self.probe())
            self.expires = self.clock() + self.ttl
        return self.value


async def serve(uid):
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / 'daemon.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    session_probe = SessionProbe(lambda: game_session(uid))
    engine = Engine(Hardware(), STATE, Path('/proc/sys/kernel/random/boot_id').read_text().strip(), session_probe)
    from fan import FanEngine, FanHardware
    fans = FanEngine(FanHardware(), STATE, atomic_json, engine.gaming, lambda: engine.last_heartbeat, engine.boot)
    from power import PowerEngine, PowerHardware
    power = PowerEngine(PowerHardware(), STATE, atomic_json, engine.gaming, lambda: engine.last_heartbeat, engine.boot)
    from autostart import Autostart
    boot_settings = Autostart(STATE, atomic_json, engine.boot, engine, power)
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
                raise PermissionError('User is not authorized to access this service')
            data = await asyncio.wait_for(reader.readline(), 3)
            if not data.endswith(b'\n') or len(data) > MAX_MESSAGE:
                raise ValueError('Request too long or incomplete')
            request = json.loads(data)
            async with serial:
                if isinstance(request, dict) and request.get('method') not in ('get_status','fan_status','power_status','boot_status','heartbeat'):
                    session_probe.invalidate()
                if isinstance(request, dict) and str(request.get('method', '')).startswith('boot_'):
                    result = boot_settings.request(request)
                else:
                    if isinstance(request, dict): boot_settings.before_manual(request)
                    if isinstance(request, dict) and str(request.get('method', '')).startswith('power_'):
                        result = power.request(request)
                    else:
                        result = fans.request(request) if isinstance(request, dict) and str(request.get('method', '')).startswith('fan_') else engine.request(request, peer_uid)
                    if isinstance(request, dict): boot_settings.after_manual(request, result)

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
                session_probe.invalidate()
                now, boot = time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)
                if (boot - last_boot) - (now - last_mono) > 1:
                    engine.last_heartbeat = now  # CPU reconnect grace; fan waits for a fresh heartbeat
                    power.on_resume()
                    fans.on_resume()
                    LOG.info('Resume detected; checking previously active fan control')
                last_mono, last_boot = now, boot
                try:
                    boot_settings.watchdog()
                    power.watchdog()
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
    LOG.info('Started backend 0.6.2 EPP-compatible; independent boot options enabled; fan autostart=%s', bool(fans.settings.get('autostart')))
    await stop.wait()
    server.close()
    await server.wait_closed()
    task.cancel()
    async with serial:
        try:
            try:
                power.restore()
            finally:
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
        parser.error('Daemon requires root; use --inspect for read-only probing')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    asyncio.run(serve(pwd.getpwnam(args.user).pw_uid))


if __name__ == '__main__':
    main()
