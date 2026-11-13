"""Transactional RAPL power limits: external changes always win."""
import copy
import json
import logging
import time
import uuid
from pathlib import Path

LOG = logging.getLogger(__name__)

class PowerHardware:
    """Discover package constraints by name. Never write MSI or the secondary domain."""
    root = Path('/sys/class/powercap')
    firmware = Path('/sys/class/firmware-attributes/msi-wmi-platform/attributes')
    board = Path('/sys/class/dmi/id/board_name')
    profile = Path('/sys/firmware/acpi/platform_profile')
    names = {'long_term': 'pl1', 'short_term': 'pl2'}

    def domains(self):
        if self.board.read_text().strip() != 'MS-1T91':
            raise ValueError('Power control is not verified for this board')
        found = {}
        for backend, pattern in [('mmio', 'intel-rapl-mmio:*'), ('msr', 'intel-rapl:*')]:
            matches = []
            for directory in sorted(self.root.glob(pattern)):
                if directory.name.count(':') != 1:
                    continue
                if (directory / 'name').read_text().strip() != 'package-0':
                    continue
                paths = {}
                for name in directory.glob('constraint_*_name'):
                    key = self.names.get(name.read_text().strip())
                    if key:
                        if key in paths:
                            raise ValueError('Ambiguous RAPL constraints')
                        paths[key] = name.with_name(name.name.replace('_name', '_power_limit_uw'))
                if set(paths) != {'pl1', 'pl2'}:
                    raise ValueError('Incomplete RAPL package constraints')
                if (directory / 'enabled').read_text().strip() != '1':
                    raise ValueError('RAPL package domain is disabled')
                matches.append(paths)
            if len(matches) > 1:
                raise ValueError('Ambiguous RAPL package domain')
            if matches:
                found[backend] = matches[0]
        if not found:
            raise ValueError('RAPL package control unavailable; MSI fallback is disabled')
        return found

    @staticmethod
    def identity(backend, paths):
        return backend + ':' + '|'.join(str(paths[k].resolve()) for k in ('pl1', 'pl2'))

    def snapshot(self):
        domains = self.domains()
        backend = 'mmio' if 'mmio' in domains else 'msr'
        readings = {name: {key: int(path.read_text()) / 1_000_000 for key, path in paths.items()}
                    for name, paths in domains.items()}
        self.selected_identity = self.identity(backend, domains[backend])
        # Conservative board-specific operating envelope, not RAPL's nominal TDP field.
        result = {**readings[backend], 'backend': backend, 'identity': self.selected_identity,
                  'pl1_min': 8, 'pl1_max': 35, 'pl2_min': 9, 'pl2_max': 45,
                  'mmio': readings.get('mmio'), 'msr': readings.get('msr')}
        try:
            result['profile'] = self.profile.read_text().strip()
        except OSError:
            result['profile'] = 'unknown'
        try:
            result['firmware'] = {key: int((self.firmware / name / 'current_value').read_text())
                                  for key, name in [('pl1', 'ppt_pl1_spl'), ('pl2', 'ppt_pl2_sppt')]}
        except (OSError, ValueError):
            result['firmware'] = None
        return result

    def write(self, key, watts):
        if key not in ('pl1', 'pl2'):
            raise ValueError('Invalid power constraint')
        domains = self.domains()
        backend = 'mmio' if 'mmio' in domains else 'msr'
        if self.identity(backend, domains[backend]) != getattr(self, 'selected_identity', None):
            raise ValueError('RAPL interface changed before write')
        (domains[backend][key]).write_text(str(round(watts * 1_000_000)))

class PowerEngine:
    def __init__(self, hw, directory, save, gaming, heartbeat, boot):
        self.hw, self.save, self.gaming, self.heartbeat, self.boot = hw, save, gaming, heartbeat, boot
        self.journal = Path(directory)/'power-transaction.json'
        self.file = Path(directory)/'power-preset.json'
        self.draft = json.loads(self.file.read_text()) if self.file.exists() else None
        self.tx = json.loads(self.journal.read_text()) if self.journal.exists() else None
        self.state, self.message = 'idle', ''
        self.revision = uuid.uuid4().hex
        self.cache = {}
        self.target = None
        self.verification = 'idle'
        self.verify_due = []
        if self.tx:
            try:
                if self.tx.get('schema') != 2:
                    self.release('Legacy MSI transaction archived; no RAPL values restored')
                elif self.tx['boot'] != boot:
                    self.release('Previous-boot power record archived; current system settings retained')
                else:
                    self.restore()
            except Exception as e:
                self.state, self.message = 'recovery_required', str(e)
    def change(self):
        self.revision = uuid.uuid4().hex
    @staticmethod
    def values(s):
        return {k:s[k] for k in ('identity','pl1','pl2')}
    def owned(self, s):
        return self.values(s) in [self.tx['last'], self.tx.get('pending')]
    def release(self, message):
        # Retain evidence but never undo another controller's decision.
        if self.journal.exists():
            self.journal.replace(self.journal.with_name('power-released-'+str(time.time_ns())+'.json'))
        self.tx = None
        self.verify_due = []
        self.verification = 'released'
        self.state, self.message = 'idle', message
        LOG.info('Power control released: %s', message)
        self.change()
    def status(self):
        try:
            s = self.hw.snapshot()
            if self.tx and not self.owned(s):
                self.release('RAPL limits or interface changed; control released')
            return {'ok':True,'revision':self.revision,'state':self.state,'message':self.message,
                    'current':s,'target':self.target,'verification':self.verification,
                    'secondary_limited':self.secondary_limited(s),'draft':self.draft,'gaming':self.gaming(),'has_transaction':bool(self.tx)}
        except Exception as e:
            return {'ok':False,'error':str(e),'revision':self.revision,'state':self.state,'has_transaction':bool(self.tx)}
    def secondary_limited(self, snapshot):
        return bool(self.target and snapshot.get('backend') == 'mmio' and snapshot.get('msr')
                    and any(snapshot['msr'][key] < self.target[key] for key in ('pl1', 'pl2')))

    def validate(self, pl1, pl2, s):
        for key,v in [('pl1',pl1),('pl2',pl2)]:
            if type(v) is not int or not s[key+'_min'] <= v <= s[key+'_max']:
                raise ValueError(key.upper()+' is outside the driver range')
        if pl2 < pl1:
            raise ValueError('PL2 must be at least PL1')
    def write(self, key, value):
        s = self.hw.snapshot()
        if not self.owned(s):
            self.release('System changed power limits during write; control released')
            raise ValueError(self.message)
        target = self.values(s); target[key] = value
        self.tx['pending'] = target
        self.save(self.journal,self.tx)
        self.hw.write(key,value)
        actual = self.values(self.hw.snapshot())
        if actual != target:
            raise ValueError('Power readback mismatch; application not confirmed')
        self.tx['last'] = actual
        self.tx.pop('pending',None)
        self.save(self.journal,self.tx)
    def pair(self, target):
        # Raise PL2 before raising PL1; lower PL1 before lowering PL2.
        order = ('pl2','pl1') if target['pl1'] > self.hw.snapshot()['pl2'] else ('pl1','pl2')
        for key in order:
            if self.hw.snapshot()[key] != target[key]: self.write(key,target[key])
    def restore(self):
        if not self.tx: return
        if not self.owned(self.hw.snapshot()):
            self.release('System changed power limits; retaining current system values')
            return
        try:
            self.pair(self.tx['baseline'])
            if self.tx:
                self.journal.unlink()
                self.tx = None
                self.verify_due = []
                self.verification = 'idle'
                self.target = None
                self.state,self.message = 'idle','Original power limits restored; control returned to system'
        except Exception as e:
            if self.tx: self.state,self.message = 'recovery_required',str(e)
            raise
        finally: self.change()
    def apply(self, pl1, pl2):
        s = self.hw.snapshot()
        self.validate(pl1,pl2,s)
        if not self.gaming() or self.heartbeat() <= 0 or time.monotonic()-self.heartbeat() > 60:
            raise ValueError('Apply in game mode with a live plugin connection')
        if self.state == 'recovery_required': raise ValueError('Recover the pending power transaction first')
        if self.tx and not self.owned(s):
            self.release('System changed power limits; review current values before applying again')
            raise ValueError(self.message)
        if not self.tx:
            self.tx = {'schema':2,'boot':self.boot,'baseline':self.values(s),'last':self.values(s)}
            self.save(self.journal,self.tx)
        self.target = {'pl1':pl1,'pl2':pl2}
        try:
            self.pair(self.target)
            if self.values(self.hw.snapshot()) != {'identity':s['identity'],'pl1':pl1,'pl2':pl2}:
                raise ValueError('Final power readback mismatch')
            self.state,self.message = 'active','Applied; control will be released if the system changes power limits'
            self.verification = 'pending'
            now = time.monotonic()
            self.verify_due = [now + 1, now + 5]
            LOG.info('Power applied: PL1=%s W PL2=%s W',pl1,pl2)
        except Exception:
            self.restore()
            raise
        finally: self.change()
    def watchdog(self):
        if not self.tx: return
        try:
            snapshot = self.hw.snapshot()
            if not self.owned(snapshot):
                self.release('RAPL limits or interface changed; plugin control stopped')
            elif not self.gaming() or time.monotonic()-self.heartbeat() > 60:
                self.restore()
            elif self.verify_due and time.monotonic() >= self.verify_due[0]:
                now = time.monotonic()
                self.verify_due = [due for due in self.verify_due if due > now]
                self.verification = 'pending' if self.verify_due else 'verified'
                self.message = 'Readback matches; limited by MSR' if self.secondary_limited(snapshot) else 'RAPL readback matches; actual consumption may be lower'
                self.change()
        except Exception as e:
            self.state,self.message = 'recovery_required',str(e)
            self.change()
    def on_resume(self):
        # Never fight firmware/Steam at resume; relinquish or restore owned limits.
        if self.tx:
            try: self.restore()
            except Exception as e:
                self.state,self.message = 'recovery_required',str(e)
    def request(self, req):
        method=req.get('method')
        if method=='power_status' and set(req)=={'method'}: return self.status()
        fields={'method','request_id','expected_revision'}
        if method in ('power_save','power_apply'): fields |= {'pl1','pl2'}
        elif method != 'power_restore': raise ValueError('Unsupported power operation')
        if set(req)!=fields: raise ValueError('Invalid power request fields')
        rid=req['request_id']
        if type(rid) is not str or not 8<=len(rid)<=128: raise ValueError('Invalid request ID')
        digest=json.dumps(req,sort_keys=True)
        if rid in self.cache:
            old,result=self.cache[rid]
            if old!=digest: raise ValueError('Request ID reused')
            return result
        if req['expected_revision']!=self.revision: raise ValueError('Power state changed; please refresh')
        try:
            if method=='power_restore': self.restore()
            else:
                self.validate(req['pl1'],req['pl2'],self.hw.snapshot())
                self.draft={k:req[k] for k in ('pl1','pl2')}
                self.save(self.file,self.draft);self.change()
                if method=='power_apply': self.apply(**self.draft)
            result=self.status()
        except Exception as e: result={**self.status(),'ok':False,'error':str(e)}
        self.cache[rid]=digest,result
        if len(self.cache)>128: del self.cache[next(iter(self.cache))]
        return result
