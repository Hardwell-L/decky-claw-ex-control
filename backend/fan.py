"""MSI dual-fan transactions. No CPU or power writes."""
import copy
import json
import time
import uuid
from pathlib import Path


def encode(percent):
    return (percent * 255 + 99) // 100


def decode(raw):
    percent = (raw * 100 + 254) // 255
    if percent * 255 // 100 != raw:
        raise ValueError('无法无损恢复 PWM')
    return percent


class FanHardware:
    def directory(self):
        if Path('/sys/class/dmi/id/board_name').read_text().strip() != 'MS-1T91':
            raise ValueError('未验证的风扇机型')
        paths = [p for p in Path('/sys/class/hwmon').glob('hwmon*')
                 if (p / 'name').read_text().strip() == 'msi_wmi_platform']
        if len(paths) != 1:
            raise ValueError('未找到唯一 MSI 风扇设备')
        return paths[0]

    def snapshot(self):
        p = self.directory()
        values = {f'pwm{f}_enable': int((p / f'pwm{f}_enable').read_text()) for f in (1, 2)}
        for f in (1, 2):
            for i in range(1, 7):
                for kind in ('temp', 'pwm'):
                    key = f'pwm{f}_auto_point{i}_{kind}'
                    values[key] = int((p / key).read_text())
        return values

    def write(self, key, value):
        valid = {'pwm1_enable'} | {f'pwm{f}_auto_point{i}_{k}' for f in (1, 2)
                                  for i in range(1, 7) for k in ('temp', 'pwm')}
        if key not in valid:
            raise ValueError('非法风扇节点')
        (self.directory() / key).write_text(str(value))

    def telemetry(self):
        p = self.directory()
        rpm = [int((p / f'fan{i}_input').read_text()) for i in (1, 2)]
        temps = [int(t.read_text()) / 1000 for h in Path('/sys/class/hwmon').glob('hwmon*')
                 if (h / 'name').read_text().strip() == 'coretemp' for t in h.glob('temp*_input')]
        if not temps:
            raise ValueError('CPU 温度不可用')
        return {'rpm': rpm, 'temperature': max(temps)}


class FanEngine:
    def __init__(self, hw, directory, atomic_json, gaming, heartbeat, boot):
        self.hw, self.save, self.gaming, self.heartbeat = hw, atomic_json, gaming, heartbeat
        self.file = Path(directory) / 'fan-presets.json'
        self.journal = Path(directory) / 'fan-transaction.json'
        self.boot = boot
        self.boot_file = Path(directory) / 'fan-boot-attempt.json'
        self.attempted_boot = json.loads(self.boot_file.read_text()).get('boot') if self.boot_file.exists() else None
        self.revision = uuid.uuid4().hex
        self.error = ''
        self.state = 'idle'
        self.cache = {}
        self.started = time.monotonic()
        self.settings = {'selected': 1, 'presets': {str(i): None for i in range(1, 5)}}
        if self.file.exists():
            self.settings = json.loads(self.file.read_text())
        self.tx = json.loads(self.journal.read_text()) if self.journal.exists() else None
        if self.tx:
            try:
                # EC settings can survive reboot. Recover before optional boot apply.
                self.restore()
            except Exception as e:
                self.state, self.error = 'recovery_required', str(e)

    def change(self):
        self.revision = uuid.uuid4().hex

    @staticmethod
    def validate(draft):
        if type(draft) is not dict or set(draft) != {'mode', 'fixed', 'points'}:
            raise ValueError('曲线字段不匹配')
        if draft['mode'] not in ('fixed', 'curve') or type(draft['fixed']) is not int or not 0 <= draft['fixed'] <= 100:
            raise ValueError('输出范围错误')
        points = draft['points']
        if type(points) is not list or len(points) != 6:
            raise ValueError('必须包含六个节点')
        previous = (-1, -1)
        for point in points:
            if type(point) is not dict or set(point) != {'temp', 'output'}:
                raise ValueError('节点字段错误')
            t, v = point['temp'], point['output']
            if type(t) is not int or type(v) is not int or not 0 <= t <= 100 or not 0 <= v <= 100:
                raise ValueError('节点范围错误')
            if t <= previous[0] or v < previous[1]:
                raise ValueError('温度必须递增，输出不能递减')
            previous = t, v

    def status(self):
        try:
            snap = self.hw.snapshot()
            telemetry = self.hw.telemetry()
            initial = {'mode': 'curve', 'fixed': 100, 'points': [
                {'temp': snap[f'pwm1_auto_point{i}_temp'], 'output': decode(snap[f'pwm1_auto_point{i}_pwm'])}
                for i in range(1, 7)]}
            return {'ok': True, 'revision': self.revision, 'state': self.state,
                    'message': self.error, 'settings': self.settings, 'initial': initial,
                    'autostart': bool(self.settings.get('autostart', False)),
                    'boot_plan': self.settings.get('boot_plan'),
                    'gaming': bool(self.gaming()), 'has_transaction': bool(self.tx),
                    'applied': self.tx.get('applied') if self.tx else None,
                    'mode': snap['pwm1_enable'], **telemetry}
        except Exception as e:
            return {'ok': False, 'revision': self.revision, 'error': str(e),
                    'state': self.state, 'has_transaction': bool(self.tx)}

    def write(self, key, value):
        # Persist both pre-write and expected post-write states for crash recovery.
        before = self.hw.snapshot()
        target = copy.deepcopy(before)
        if key == 'pwm1_enable':
            target['pwm1_enable'] = target['pwm2_enable'] = value
        else:
            target[key] = value * 100 // 255 * 255 // 100 if key.endswith('_pwm') else value
        self.tx['pending'] = {'before': before, 'after': target, 'mode_reset': key == 'pwm1_enable' and value == 2}
        self.save(self.journal, self.tx)
        self.hw.write(key, value)
        actual = self.hw.snapshot()
        if key == 'pwm1_enable':
            if actual['pwm1_enable'] != value or actual['pwm2_enable'] != value:
                raise ValueError('风扇模式回读失败')
        elif actual != target:
            raise ValueError('风扇曲线回读失败')
        self.tx['last'] = actual
        self.tx.pop('pending', None)
        self.save(self.journal, self.tx)

    def owned(self, current):
        if current == self.tx['last']:
            return True
        pending = self.tx.get('pending', {})
        # Mode 2 can reset all points to driver-probe values. A crash here is
        # ambiguous, so retain evidence and refuse automatic overwrites.
        return current in [pending.get('before'), pending.get('after')]

    def restore(self):
        if not self.tx:
            return
        current = self.hw.snapshot()
        if not self.owned(current):
            self.state = 'conflict'
            raise ValueError('风扇配置被外部改变或恢复状态不明确；保留备份，未覆盖')
        self.state = 'restoring'
        try:
            self.write('pwm1_enable', 2)
            baseline = self.tx['baseline']
            for key, raw in baseline.items():
                if key.endswith('_enable'):
                    continue
                if self.hw.snapshot()[key] != raw:
                    self.write(key, encode(decode(raw)) if key.endswith('_pwm') else raw)
            if self.hw.snapshot() != baseline:
                raise ValueError('恢复回读不一致')
            self.journal.unlink()
            self.tx = None
            self.state, self.error = 'idle', ''
        except Exception as e:
            self.state, self.error = 'recovery_required', str(e)
            raise
        finally:
            self.change()

    def apply(self, slot, draft):
        self.validate(draft)
        if not self.gaming() or time.monotonic() - self.heartbeat() > 60:
            raise ValueError('仅在游戏模式且插件心跳正常时应用')
        outputs = [draft['fixed']] * 6 if draft['mode'] == 'fixed' else [p['output'] for p in draft['points']]
        if self.state not in ('idle', 'active'):
            raise ValueError('请先解决风扇恢复或控制冲突')
        snap = self.hw.snapshot()
        if self.tx and not self.owned(snap):
            raise ValueError('外部风扇配置变化，停止应用')
        if not self.tx:
            if snap['pwm1_enable'] != 2 or snap['pwm2_enable'] != 2:
                raise ValueError('当前由其他自定义控制接管，请先恢复自动')
            for key, raw in snap.items():
                if key.endswith('_pwm'):
                    decode(raw)
            self.tx = {'boot': self.boot, 'baseline': snap, 'last': snap}
            self.save(self.journal, self.tx)
        self.state = 'applying'
        try:
            self.write('pwm1_enable', 2)
            for f in (1, 2):
                for i, point in enumerate(draft['points'], 1):
                    self.write(f'pwm{f}_auto_point{i}_temp', point['temp'])
                    self.write(f'pwm{f}_auto_point{i}_pwm', encode(outputs[i-1]))
            self.write('pwm1_enable', 1)
            self.tx['applied'] = {'slot': slot, 'draft': copy.deepcopy(draft)}
            self.save(self.journal, self.tx)
            self.state, self.error = 'active', ''
            self.started = time.monotonic()
            if self.settings.get('autostart'):
                settings = copy.deepcopy(self.settings)
                settings['boot_plan'] = copy.deepcopy(self.tx['applied'])
                self.save(self.file, settings)
                self.settings = settings
        except Exception:
            self.restore()
            raise
        finally:
            self.change()

    def consume_boot(self):
        self.save(self.boot_file, {'boot': self.boot})
        self.attempted_boot = self.boot

    def set_autostart(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('开机应用开关必须为布尔值')
        settings = copy.deepcopy(self.settings)
        if enabled:
            if self.state != 'active' or not self.tx or not self.tx.get('applied'):
                raise ValueError('请先成功应用一次风扇方案，再开启开机恢复')
            settings['boot_plan'] = copy.deepcopy(self.tx['applied'])
        settings['autostart'] = enabled
        # Changing this option never triggers an application in this boot.
        self.consume_boot()
        self.save(self.file, settings)
        self.settings = settings
        self.change()

    def watchdog(self):
        if not self.tx:
            if (self.state == 'idle' and self.settings.get('autostart')
                    and self.attempted_boot != self.boot and self.gaming()
                    and self.heartbeat() > 0 and time.monotonic() - self.heartbeat() <= 60):
                try:
                    # Persist before touching hardware: never loop after failure/crash.
                    self.consume_boot()
                    plan = self.settings.get('boot_plan')
                    if not isinstance(plan, dict) or type(plan.get('slot')) is not int or plan['slot'] not in (1,2):
                        raise ValueError('开机方案无效，请重新应用并设置开关')
                    self.apply(plan['slot'], plan['draft'])
                except Exception as e:
                    self.error = '开机应用失败，本次开机不重试：' + str(e)
                    self.change()
            return
        try:
            if self.hw.snapshot() != self.tx['last']:
                self.state = 'conflict'
                self.error = '检测到外部风扇配置变化，已停止控制'
                return
            if not self.gaming() or time.monotonic() - self.heartbeat() > 60:
                self.restore()
                return
            try:
                telemetry = self.hw.telemetry()
                if time.monotonic() - self.started >= 10 and any(rpm <= 0 for rpm in telemetry['rpm']):
                    raise ValueError('自定义输出 10 秒后仍缺少双风扇转速反馈')
            except Exception as e:
                self.restore()
                self.error = '已恢复自动：' + str(e)
        except Exception as e:
            self.error = str(e)

    def request(self, req):
        methods = {'fan_status': set(), 'fan_save': {'slot', 'draft'}, 'fan_apply': {'slot', 'draft'}, 'fan_restore': set(), 'fan_autostart': {'enabled'}}
        method = req.get('method')
        fields = {'method'} if method == 'fan_status' else {'method', 'request_id', 'expected_revision'}
        if method not in methods or set(req) != fields | methods[method]:
            raise ValueError('风扇请求字段不匹配')
        if method == 'fan_status':
            return self.status()
        rid = req['request_id']
        if type(rid) is not str or not 8 <= len(rid) <= 128:
            raise ValueError('无效请求编号')
        digest = json.dumps(req, sort_keys=True)
        if rid in self.cache:
            old, result = self.cache[rid]
            if old != digest:
                raise ValueError('请求编号重复')
            return result
        if req['expected_revision'] != self.revision:
            raise ValueError('风扇状态已变化，请刷新')
        try:
            if method == 'fan_autostart':
                self.set_autostart(req['enabled'])
            elif method == 'fan_restore':
                self.consume_boot()
                self.restore()
            else:
                slot, draft = req['slot'], req['draft']
                if type(slot) is not int or not 1 <= slot <= 4:
                    raise ValueError('无效方案')
                self.validate(draft)
                settings = copy.deepcopy(self.settings)
                settings['selected'] = slot
                settings['presets'][str(slot)] = copy.deepcopy(draft)
                self.save(self.file, settings)
                self.settings = settings
                self.change()
                if method == 'fan_apply':
                    self.consume_boot()
                    self.apply(slot, draft)
            result = self.status()
        except Exception as e:
            result = {**self.status(), 'ok': False, 'error': str(e)}
        self.cache[rid] = digest, result
        if len(self.cache) > 128:
            del self.cache[next(iter(self.cache))]
        return result
