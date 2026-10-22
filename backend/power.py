"""Cooperative MSI firmware power limits: external changes always win."""
import copy
import json
import logging
import time
import uuid
from pathlib import Path

LOG = logging.getLogger(__name__)

class PowerHardware:
    root = Path('/sys/class/firmware-attributes/msi-wmi-platform/attributes')
    names = {'pl1': 'ppt_pl1_spl', 'pl2': 'ppt_pl2_sppt'}
    def snapshot(self):
        if Path('/sys/class/dmi/id/board_name').read_text().strip() != 'MS-1T91':
            raise ValueError('Power control is not verified for this board')
        result = {'profile': Path('/sys/firmware/acpi/platform_profile').read_text().strip()}
        for key, name in self.names.items():
            p = self.root / name
            result[key] = int((p/'current_value').read_text())
            result[key+'_min'] = int((p/'min_value').read_text())
            result[key+'_max'] = int((p/'max_value').read_text())
        return result
    def write(self, key, watts):
        (self.root/self.names[key]/'current_value').write_text(str(watts))

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
        if self.tx:
            try:
                if self.tx['boot'] != boot:
                    self.release('Previous-boot power record archived; current system settings retained')
                else:
                    self.restore()
            except Exception as e:
                self.state, self.message = 'recovery_required', str(e)
    def change(self):
        self.revision = uuid.uuid4().hex
    @staticmethod
    def values(s):
        return {k:s[k] for k in ('profile','pl1','pl2')}
    def owned(self, s):
        return self.values(s) in [self.tx['last'], self.tx.get('pending')]
    def release(self, message):
        # Retain evidence but never undo another controller's decision.
        if self.journal.exists():
            self.journal.replace(self.journal.with_name('power-released-'+str(time.time_ns())+'.json'))
        self.tx = None
        self.state, self.message = 'idle', message
        LOG.info('Power control released: %s', message)
        self.change()
    def status(self):
        try:
            s = self.hw.snapshot()
            if self.tx and not self.owned(s):
                self.release('System power or performance profile changed; control released')
            return {'ok':True,'revision':self.revision,'state':self.state,'message':self.message,
                    'current':s,'draft':self.draft,'gaming':self.gaming(),'has_transaction':bool(self.tx)}
        except Exception as e:
            return {'ok':False,'error':str(e),'revision':self.revision,'state':self.state,'has_transaction':bool(self.tx)}
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
        if s['profile'] != 'custom':
            raise ValueError('Enable custom TDP in Steam Performance settings before applying power limits')
        if self.state == 'recovery_required': raise ValueError('Recover the pending power transaction first')
        if self.tx and not self.owned(s):
            self.release('System changed power limits; review current values before applying again')
            raise ValueError(self.message)
        if not self.tx:
            self.tx = {'boot':self.boot,'baseline':self.values(s),'last':self.values(s)}
            self.save(self.journal,self.tx)
        try:
            self.pair({'pl1':pl1,'pl2':pl2})
            if self.values(self.hw.snapshot()) != {'profile':'custom','pl1':pl1,'pl2':pl2}:
                raise ValueError('Final power readback mismatch')
            self.state,self.message = 'active','Applied; control will be released if the system changes power limits'
            LOG.info('Power applied: PL1=%s W PL2=%s W',pl1,pl2)
        except Exception:
            self.restore()
            raise
        finally: self.change()
    def watchdog(self):
        if not self.tx: return
        try:
            if not self.owned(self.hw.snapshot()):
                self.release('System power or performance profile changed; plugin control stopped')
            elif not self.gaming() or time.monotonic()-self.heartbeat() > 60:
                self.restore()
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
