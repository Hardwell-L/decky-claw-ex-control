import importlib.util
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from test_engine import d
spec=importlib.util.spec_from_file_location('autostart',Path(__file__).parents[1]/'backend/autostart.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class CPU:
 def __init__(self):self.game=True;self.last_heartbeat=time.monotonic();self.calls=[];self.tx=None;self.fail=False
 def gaming(self):return self.game
 def apply(self,name,values):
  self.calls.append(('cpu',name,values))
  if self.fail:raise ValueError('injected')
 def set_turbo(self,value):self.calls.append(('turbo',value))
class Power:
 def __init__(self):self.profile='custom';self.hw=self;self.calls=[];self.state='active'
 def snapshot(self):return {'profile':self.profile}
 def apply(self,**kw):self.calls.append(kw)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.cpu=CPU();self.power=Power();self.e=self.engine()
 def engine(self,boot='boot'):return a.Autostart(self.tmp.name,d.atomic_json,boot,self.cpu,self.power)
 def toggle(self,key,enabled):return self.e.request(dict(method='boot_set',feature=key,enabled=enabled,request_id=uuid.uuid4().hex,expected_revision=self.e.revision))
 def ready(self):
  self.e.record('cpu',{'name':'default','p_max_khz':2700000,'e_max_khz':1900000})
  self.e.record('turbo',False);self.e.record('power',{'pl1':17,'pl2':25})
  for k in self.e.features:self.toggle(k,True)
 def test_default_off(self):
  self.e.watchdog();self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_requires_successful_setting(self):
  with self.assertRaises(ValueError):self.toggle('cpu',True)
 def test_toggle_never_applies_this_boot(self):
  self.ready();self.e.watchdog();self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_independent_options(self):
  self.ready();self.toggle('cpu',False);self.e=self.engine('new');self.e.watchdog()
  self.assertEqual(self.cpu.calls,[('turbo',False)]);self.assertEqual(self.power.calls,[{'pl1':17,'pl2':25}])
 def test_waits_for_gaming_heartbeat(self):
  self.ready();self.e=self.engine('new');self.cpu.game=False;self.e.watchdog();self.assertFalse(self.cpu.calls)
  self.cpu.game=True;self.cpu.last_heartbeat=0;self.e.watchdog();self.assertFalse(self.cpu.calls)
  self.cpu.last_heartbeat=time.monotonic();self.e.watchdog();self.assertEqual(len(self.cpu.calls),2)
 def test_cpu_before_turbo_and_once_per_boot(self):
  self.ready();self.e=self.engine('new');self.e.watchdog();self.e.watchdog();self.e=self.engine('new');self.e.watchdog()
  self.assertEqual([x[0] for x in self.cpu.calls],['cpu','turbo']);self.assertEqual(len(self.power.calls),1)
 def test_power_waits_custom(self):
  self.ready();self.e=self.engine('new');self.power.profile='performance';self.e.watchdog()
  self.assertFalse(self.power.calls);self.power.profile='custom';self.e.watchdog();self.assertEqual(len(self.power.calls),1)
 def test_failed_cpu_not_retried_others_work(self):
  self.ready();self.e=self.engine('new');self.cpu.fail=True;self.e.watchdog();self.e.watchdog()
  self.assertEqual(len(self.cpu.calls),2);self.assertIn('injected',self.e.entry('cpu')['error']);self.assertEqual(len(self.power.calls),1)
 def test_restore_cancels_pending(self):
  self.ready();self.e=self.engine('new');self.e.before_manual({'method':'restore'});self.e.before_manual({'method':'power_restore'});self.e.watchdog()
  self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_draft_or_failure_never_updates_last(self):
  self.ready();before=self.e.entry('power')['last'].copy()
  for method,result in [('power_save',{'ok':True}),('power_apply',{'ok':False})]:
   self.e.after_manual({'method':method,'pl1':12,'pl2':15},result)
  self.assertEqual(self.e.entry('power')['last'],before)
 def test_success_updates_next_boot_not_current(self):
  self.ready();self.e.before_manual({'method':'power_apply'});self.e.after_manual({'method':'power_apply','pl1':12,'pl2':15},{'ok':True})
  self.e.watchdog();self.assertFalse(self.power.calls)
  self.e=self.engine('new');self.e.watchdog();self.assertEqual(self.power.calls,[{'pl1':12,'pl2':15}])
 def test_false_turbo_is_valid_saved_value(self):
  self.e.record('turbo',False);self.toggle('turbo',True);self.e=self.engine('new');self.e.watchdog()
  self.assertEqual(self.cpu.calls,[('turbo',False)])
 def test_system_takes_power_no_reapply(self):
  self.ready();self.e=self.engine('new');self.e.watchdog();self.power.state='idle';self.e.watchdog()
  self.assertEqual(len(self.power.calls),1)
