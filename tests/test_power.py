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
  self.data={'pl1':17,'pl2':17,'pl1_min':8,'pl1_max':35,'pl2_min':9,'pl2_max':45,'profile':'custom','identity':'mmio:test','backend':'mmio','msr':{'pl1':40,'pl2':50}}
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
 def test_interface_change_releases(self):
  self.e.apply(20,30);self.hw.data['identity']='msr:other';n=len(self.hw.writes)
  self.e.watchdog();self.assertIsNone(self.e.tx);self.assertEqual(len(self.hw.writes),n)
 def test_save_and_boot_do_not_apply(self):
  self.assertTrue(self.req('power_save',pl1=20,pl2=30)['ok']);self.e=self.engine('new')
  self.e.watchdog();self.assertFalse(self.hw.writes);self.assertEqual(self.e.draft,{'pl1':20,'pl2':30})
 def test_invalid_limits(self):
  for a,b in [(7,20),(36,40),(10,46),(20,19),(True,20),(20.0,30)]:
   self.assertFalse(self.req('power_apply',pl1=a,pl2=b)['ok'])
  self.assertFalse(self.hw.writes)
 def test_desktop_rejected_profile_independent(self):
  self.game=False
  with self.assertRaises(ValueError):self.e.apply(20,30)
  self.game=True;self.hw.data['profile']='performance'
  self.e.apply(20,30)
  self.assertEqual(self.hw.data['pl1'],20)
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

 def test_delayed_verification_and_secondary_ceiling(self):
  from unittest.mock import patch
  self.e.apply(20,30);self.assertEqual(self.e.status()['verification'],'pending')
  first,last=self.e.verify_due
  with patch.object(p.time,'monotonic',return_value=first):self.e.watchdog()
  self.assertEqual(self.e.verification,'pending')
  self.hw.data['msr']['pl1']=12
  with patch.object(p.time,'monotonic',return_value=last):self.e.watchdog()
  self.assertEqual(self.e.verification,'verified')
  self.assertTrue(self.e.status()['secondary_limited'])
  self.assertEqual(self.hw.data['msr']['pl1'],12)
  self.assertEqual(self.hw.writes,[('pl2',30),('pl1',20)])

 def test_overwrite_before_recheck_preserves_target(self):
  self.e.apply(20,30);self.hw.data['pl1']=35
  n=len(self.hw.writes);self.e.watchdog();status=self.e.status()
  self.assertEqual(status['verification'],'released')
  self.assertEqual(status['target'],{'pl1':20,'pl2':30})
  self.assertEqual(status['current']['pl1'],35)
  self.assertEqual(len(self.hw.writes),n)

 def test_restore_exact_fractional_baseline_above_ui_range(self):
  self.hw.data.update(pl1=40.125,pl2=50.25)
  self.e.apply(12,15);self.e.restore()
  self.assertEqual(self.hw.data['pl1'],40.125)
  self.assertEqual(self.hw.data['pl2'],50.25)

 def test_legacy_msi_journal_never_restored_to_rapl(self):
  old={'boot':'boot','baseline':{'profile':'custom','pl1':8,'pl2':9},
       'last':{'profile':'custom','pl1':17,'pl2':17}}
  d.atomic_json(self.e.journal,old);self.e=self.engine()
  self.assertIsNone(self.e.tx);self.assertFalse(self.hw.writes)
  self.assertTrue(list(Path(self.tmp.name).glob('power-released-*')))

class DiscoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.hw=p.PowerHardware();self.hw.root=self.root/'powercap';self.hw.root.mkdir()
  self.hw.board=self.root/'board';self.hw.board.write_text('MS-1T91')
  self.hw.profile=self.root/'profile';self.hw.profile.write_text('balanced')
  self.hw.firmware=self.root/'firmware'
 def domain(self,name,pl1=40000000,pl2=50000000):
  folder=self.hw.root/name;folder.mkdir()
  for key,value in {'name':'package-0','enabled':'1','constraint_7_name':'long_term',
                    'constraint_3_name':'short_term','constraint_7_power_limit_uw':pl1,
                    'constraint_3_power_limit_uw':pl2}.items():
   (folder/key).write_text(str(value))
  return folder
 def test_prefers_mmio_and_resolves_constraint_names(self):
  msr=self.domain('intel-rapl:0');mmio=self.domain('intel-rapl-mmio:0')
  s=self.hw.snapshot();self.assertEqual(s['backend'],'mmio')
  self.assertEqual(s['pl1'],40);self.assertEqual(s['msr']['pl2'],50)
  self.assertIsNone(s['firmware'])
  self.hw.write('pl1',12)
  self.assertEqual((mmio/'constraint_7_power_limit_uw').read_text(),'12000000')
  self.assertEqual((msr/'constraint_7_power_limit_uw').read_text(),'40000000')
 def test_msr_fallback_without_mmio(self):
  self.domain('intel-rapl:0');self.assertEqual(self.hw.snapshot()['backend'],'msr')
 def test_no_msi_fallback(self):
  with self.assertRaisesRegex(ValueError,'unavailable'):self.hw.snapshot()
 def test_disabled_domain_refused(self):
  mmio=self.domain('intel-rapl-mmio:0');(mmio/'enabled').write_text('0')
  with self.assertRaisesRegex(ValueError,'disabled'):self.hw.snapshot()
 def test_missing_constraint_refused(self):
  mmio=self.domain('intel-rapl-mmio:0');(mmio/'constraint_3_name').unlink()
  with self.assertRaisesRegex(ValueError,'Incomplete'):self.hw.snapshot()
 def test_interface_switch_between_read_and_write_refused(self):
  msr=self.domain('intel-rapl:0');self.hw.snapshot();self.domain('intel-rapl-mmio:0')
  with self.assertRaisesRegex(ValueError,'changed'):self.hw.write('pl1',12)
  self.assertEqual((msr/'constraint_7_power_limit_uw').read_text(),'40000000')
 def test_wrong_board_refused(self):
  self.hw.board.write_text('OTHER')
  with self.assertRaisesRegex(ValueError,'not verified'):self.hw.snapshot()
 def test_fractional_watts_restore_microwatt_units_exactly(self):
  mmio=self.domain('intel-rapl-mmio:0',40123456,50123456)
  s=self.hw.snapshot();self.hw.write('pl1',12);self.hw.write('pl1',s['pl1'])
  self.assertEqual((mmio/'constraint_7_power_limit_uw').read_text(),'40123456')
