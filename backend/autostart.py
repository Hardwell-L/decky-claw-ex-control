"""Once-per-boot opt-in restore of successfully applied settings."""
import copy
import json
import logging
import time
import uuid
from pathlib import Path

LOG=logging.getLogger(__name__)

class Autostart:
    features = ('cpu','turbo','power')
    def __init__(self, directory, save, boot, cpu, power):
        self.file=Path(directory)/'autostart.json'
        self.save,self.boot,self.cpu,self.power=save,boot,cpu,power
        self.data=json.loads(self.file.read_text()) if self.file.exists() else {}
        self.revision=uuid.uuid4().hex
        self.cache={}
    def persist(self):
        self.save(self.file,self.data);self.revision=uuid.uuid4().hex
    def entry(self,key):
        return self.data.setdefault(key,{'enabled':False,'last':None,'attempted':None,'error':''})
    def consume(self,key):
        self.entry(key)['attempted']=self.boot;self.persist()
    def record(self,key,value):
        self.entry(key)['last']=copy.deepcopy(value)
        self.entry(key)['error']='';self.persist()
    def status(self):
        return {'ok':True,'revision':self.revision,'features':copy.deepcopy({k:self.entry(k) for k in self.features})}
    def request(self,req):
        if req=={'method':'boot_status'}:return self.status()
        if set(req)!={'method','feature','enabled','expected_revision','request_id'} or req['method']!='boot_set':
            raise ValueError('Invalid startup request fields')
        key,enabled= req['feature'],req['enabled']
        if key not in self.features or type(enabled) is not bool:raise ValueError('Invalid startup setting')
        rid=req['request_id'];digest=json.dumps(req,sort_keys=True)
        if type(rid) is not str or not 8<=len(rid)<=128:raise ValueError('Invalid request ID')
        if rid in self.cache:
            old,result=self.cache[rid]
            if old!=digest:raise ValueError('Request ID reused')
            return result
        if req['expected_revision']!=self.revision:raise ValueError('State changed; please retry')
        if enabled and self.entry(key)['last'] is None:raise ValueError('Apply this feature successfully before enabling startup restore')
        self.entry(key)['enabled']=enabled
        self.entry(key)['error']=''
        self.consume(key)  # Never apply merely because an option was toggled.
        result=self.status();self.cache[rid]=(digest,result)
        if len(self.cache)>128:del self.cache[next(iter(self.cache))]
        return result
    def before_manual(self, req):
        method=req.get('method')
        keys={'apply':('cpu',),'configure':('cpu',),'set_turbo':('turbo',),
              'restore':('cpu','turbo'),'power_apply':('power',),'power_restore':('power',)}.get(method,())
        for key in keys:self.consume(key)
    def after_manual(self,req,result):
        if not result.get('ok'):return
        method=req.get('method')
        if method in ('apply','configure') and self.cpu.gaming() and self.cpu.tx and self.cpu.tx.get('applied'):
            self.record('cpu',self.cpu.tx['applied'])
        elif method=='set_turbo':self.record('turbo',req['enabled'])
        elif method=='power_apply' and self.power.state=='active':
            self.record('power',{k:req[k] for k in ('pl1','pl2')})
    def watchdog(self):
        if not self.cpu.gaming() or self.cpu.last_heartbeat<=0 or time.monotonic()-self.cpu.last_heartbeat>60:return
        for key in self.features:
            e=self.entry(key)
            if not e['enabled'] or e['attempted']==self.boot:continue
            # Wait for Steam to select custom mode; never change platform profile.
            if key=='power':
                try:
                    if self.power.hw.snapshot()['profile']!='custom':continue
                except Exception:continue
            self.consume(key)  # Durable, before any writes: no repeated attempts on restart.
            try:
                value=e['last']
                if key=='cpu':self.cpu.apply(value['name'],{k:value[k] for k in ('p_max_khz','e_max_khz')})
                elif key=='turbo':self.cpu.set_turbo(value)
                else:self.power.apply(**value)
                e['error']=''
                LOG.info('Boot application succeeded: %s', key)
            except Exception as exc:
                e['error']='Startup application failed; no retry this boot: '+str(exc)
                LOG.exception('Boot application failed: %s', key)
            self.persist()
