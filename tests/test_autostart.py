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
 def settled(self):
  self.e.watchdog();self.e.power_since-=11;self.e.watchdog()
 def toggle(self,key,enabled):return self.e.request(dict(method='boot_set',feature=key,enabled=enabled,request_id=uuid.uuid4().hex,expected_revision=self.e.revision))
 def ready(self):
  self.e.record('cpu',{'name':'default','p_max_khz':2700000,'e_max_khz':1900000})
  self.e.record('turbo',False);self.e.record('power',{'pl1':17,'pl2':25})
  for k in self.e.features:self.toggle(k,True)
 def test_default_off(self):
  self.settled();self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_requires_successful_setting(self):
  with self.assertRaises(ValueError):self.toggle('cpu',True)
 def test_toggle_never_applies_this_boot(self):
  self.ready();self.settled();self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_independent_options(self):
  self.ready();self.toggle('cpu',False);self.e=self.engine('new');self.settled()
  self.assertEqual(self.cpu.calls,[('turbo',False)]);self.assertEqual(self.power.calls,[{'pl1':17,'pl2':25}])
 def test_waits_for_gaming_heartbeat(self):
  self.ready();self.e=self.engine('new');self.cpu.game=False;self.settled();self.assertFalse(self.cpu.calls)
  self.cpu.game=True;self.cpu.last_heartbeat=0;self.settled();self.assertFalse(self.cpu.calls)
  self.cpu.last_heartbeat=time.monotonic();self.settled();self.assertEqual(len(self.cpu.calls),2)
 def test_cpu_before_turbo_and_once_per_boot(self):
  self.ready();self.e=self.engine('new');self.settled();self.settled();self.e=self.engine('new');self.settled()
  self.assertEqual([x[0] for x in self.cpu.calls],['cpu','turbo']);self.assertEqual(len(self.power.calls),1)
 def test_power_independent_of_custom_profile(self):
  self.ready();self.e=self.engine('new');self.power.profile='performance';self.settled()
  self.assertEqual(len(self.power.calls),1)
 def test_failed_cpu_not_retried_others_work(self):
  self.ready();self.e=self.engine('new');self.cpu.fail=True;self.settled();self.settled()
  self.assertEqual(len(self.cpu.calls),2);self.assertIn('injected',self.e.entry('cpu')['error']);self.assertEqual(len(self.power.calls),1)
 def test_restore_cancels_pending(self):
  self.ready();self.e=self.engine('new');self.e.before_manual({'method':'restore'});self.e.before_manual({'method':'power_restore'});self.settled()
  self.assertFalse(self.cpu.calls);self.assertFalse(self.power.calls)
 def test_draft_or_failure_never_updates_last(self):
  self.ready();before=self.e.entry('power')['last'].copy()
  for method,result in [('power_save',{'ok':True}),('power_apply',{'ok':False})]:
   self.e.after_manual({'method':method,'pl1':12,'pl2':15},result)
  self.assertEqual(self.e.entry('power')['last'],before)
 def test_success_updates_next_boot_not_current(self):
  self.ready();self.e.before_manual({'method':'power_apply'});self.e.after_manual({'method':'power_apply','pl1':12,'pl2':15},{'ok':True})
  self.settled();self.assertFalse(self.power.calls)
  self.e=self.engine('new');self.settled();self.assertEqual(self.power.calls,[{'pl1':12,'pl2':15}])
 def test_false_turbo_is_valid_saved_value(self):
  self.e.record('turbo',False);self.toggle('turbo',True);self.e=self.engine('new');self.settled()
  self.assertEqual(self.cpu.calls,[('turbo',False)])
 def test_system_takes_power_no_reapply(self):
  self.ready();self.e=self.engine('new');self.settled();self.power.state='idle';self.settled()
  self.assertEqual(len(self.power.calls),1)

 def test_power_startup_waits_for_stable_values(self):
  from unittest.mock import patch
  self.ready();self.e=self.engine('new');now=[1000.0];self.cpu.last_heartbeat=1000.0
  current={'profile':'custom','pl1':35,'pl2':35}
  self.power.snapshot=lambda:current.copy()
  with patch.object(a.time,'monotonic',side_effect=lambda:now[0]):
   self.e.watchdog();self.assertFalse(self.power.calls)
   now[0]+=2;current['pl1']=20;self.e.watchdog();self.assertFalse(self.power.calls)
   now[0]+=9;self.e.watchdog();self.assertFalse(self.power.calls)
   now[0]+=1;self.e.watchdog();self.assertEqual(len(self.power.calls),1)
   self.e.watchdog();self.assertEqual(len(self.power.calls),1)

 def test_reenter_game_restores_enabled_features_once(self):
  self.ready();self.e=self.engine('new');self.settled()
  self.cpu.game=False;self.e.watchdog();self.cpu.game=True;self.settled()
  self.assertEqual(len(self.cpu.calls),2)
  self.cpu.last_heartbeat=time.monotonic();self.settled();self.settled()
  self.assertEqual(len(self.power.calls),2);self.assertEqual(len(self.cpu.calls),4)
 def test_resume_rearms_enabled_power_once(self):
  self.ready();self.e=self.engine('new');self.settled()
  self.e.on_resume();self.settled();self.settled()
  self.assertEqual(len(self.power.calls),2)
 def test_disabled_power_does_not_resume(self):
  self.ready();self.toggle('power',False);self.e.on_resume();self.settled()
  self.assertFalse(self.power.calls)
 def test_manual_restore_cancels_resume(self):
  self.ready();self.e.on_resume();self.e.before_manual({'method':'power_restore'});self.settled()
  self.assertFalse(self.power.calls)
