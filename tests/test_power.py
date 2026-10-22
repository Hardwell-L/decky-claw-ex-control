import copy
import importlib.util
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from test_engine import d
spec=importlib.util.spec_from_file_location('power',Path(__file__).parents[1]/'backend/power.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class HW:
 def __init__(self):
  self.data={'pl1':17,'pl2':17,'pl1_min':8,'pl1_max':35,'pl2_min':9,'pl2_max':45,'profile':'custom'}
  self.writes=[];self.fail=None
 def snapshot(self):return copy.deepcopy(self.data)
 def write(self,k,v):
  if k==self.fail:self.fail=None;raise OSError('injected failure')
  self.data[k]=v;self.writes.append((k,v))
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.hw=HW();self.game=True;self.beat=time.monotonic();self.e=self.engine()
 def engine(self,boot='boot'):return p.PowerEngine(self.hw,self.tmp.name,d.atomic_json,lambda:self.game,lambda:self.beat,boot)
 def req(self,method,**kw):return self.e.request(dict(method=method,request_id=uuid.uuid4().hex,expected_revision=self.e.revision,**kw))
 def test_apply_restore(self):
  self.e.apply(20,30);self.assertEqual(self.hw.writes,[('pl2',30),('pl1',20)])
  self.e.restore();self.assertEqual((self.hw.data['pl1'],self.hw.data['pl2']),(17,17))
 def test_lower_pl1_first(self):
  self.e.apply(10,12);self.assertEqual(self.hw.writes,[('pl1',10),('pl2',12)])
 def test_second_write_failure_compensates(self):
  self.hw.fail='pl1'
  with self.assertRaises(OSError):self.e.apply(20,30)
  self.assertEqual((self.hw.data['pl1'],self.hw.data['pl2']),(17,17));self.assertIsNone(self.e.tx)
 def test_external_wins_no_fight(self):
  self.e.apply(20,30);self.hw.data['pl1']=self.hw.data['pl2']=15
  n=len(self.hw.writes);self.e.watchdog();self.e.watchdog();self.e.restore()
  self.assertIsNone(self.e.tx);self.assertEqual(len(self.hw.writes),n)
  self.assertEqual(self.hw.data['pl2'],15)
 def test_profile_change_releases(self):
  self.e.apply(20,30);self.hw.data['profile']='balanced';n=len(self.hw.writes)
  self.e.watchdog();self.assertIsNone(self.e.tx);self.assertEqual(len(self.hw.writes),n)
 def test_save_and_boot_do_not_apply(self):
  self.assertTrue(self.req('power_save',pl1=20,pl2=30)['ok']);self.e=self.engine('new')
  self.e.watchdog();self.assertFalse(self.hw.writes);self.assertEqual(self.e.draft,{'pl1':20,'pl2':30})
 def test_invalid_limits(self):
  for a,b in [(7,20),(36,40),(10,46),(20,19),(True,20),(20.0,30)]:
   self.assertFalse(self.req('power_apply',pl1=a,pl2=b)['ok'])
  self.assertFalse(self.hw.writes)
 def test_desktop_and_profile_rejected(self):
  self.game=False
  with self.assertRaises(ValueError):self.e.apply(20,30)
  self.game=True;self.hw.data['profile']='performance'
  with self.assertRaises(ValueError):self.e.apply(20,30)
  self.assertFalse(self.hw.writes)
 def test_heartbeat_loss_restores(self):
  self.e.apply(20,30);self.beat-=61;self.e.watchdog();self.assertIsNone(self.e.tx)
  self.assertEqual(self.hw.data['pl1'],17)
 def test_resume_returns_control(self):
  self.e.apply(20,30);self.e.on_resume();self.assertIsNone(self.e.tx)
  self.assertEqual(self.hw.data['pl2'],17)
 def test_resume_preserves_firmware_reset(self):
  self.e.apply(20,30);self.hw.data['pl1']=12;n=len(self.hw.writes)
  self.e.on_resume();self.assertEqual(len(self.hw.writes),n);self.assertIsNone(self.e.tx)
 def test_same_boot_restart_restores(self):
  self.e.apply(20,30);self.e=self.engine();self.assertEqual(self.hw.data['pl1'],17)
 def test_new_boot_never_restores_old_values(self):
  self.e.apply(20,30);n=len(self.hw.writes);self.e=self.engine('new')
  self.assertIsNone(self.e.tx);self.assertEqual(len(self.hw.writes),n)
 def test_revision_and_replay(self):
  r=dict(method='power_apply',pl1=20,pl2=30,request_id=uuid.uuid4().hex,expected_revision=self.e.revision)
  result=self.e.request(r);n=len(self.hw.writes)
  self.assertEqual(self.e.request(r),result);self.assertEqual(len(self.hw.writes),n)
  r['request_id']=uuid.uuid4().hex
  with self.assertRaises(ValueError):self.e.request(r)
 def test_restore_failure_retains_journal(self):
  self.e.apply(20,30);self.hw.fail='pl1'
  with self.assertRaises(OSError):self.e.restore()
  self.assertTrue(self.e.journal.exists());self.e.restore();self.assertIsNone(self.e.tx)

 def test_external_write_between_pair_stops_remaining_write(self):
  original=self.hw.write
  def external(k,v):
   original(k,v)
   if k=='pl2':self.hw.data['pl1']=14
  self.hw.write=external
  with self.assertRaises(ValueError):self.e.apply(20,30)
  self.assertEqual(self.hw.writes,[('pl2',30)])
  self.assertEqual(self.hw.data['pl1'],14)
  self.assertIsNone(self.e.tx)
