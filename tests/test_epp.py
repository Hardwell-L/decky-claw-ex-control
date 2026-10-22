import copy,json,tempfile,time,unittest
from pathlib import Path
from test_engine import d,Hardware
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.hw=Hardware();self.original=self.hw.discover()
  self.e=d.Engine(self.hw,self.tmp.name,'boot',lambda:True);self.e.last_heartbeat=time.monotonic()
  self.values={'p_max_khz':2000000,'e_max_khz':1400000}
 def change_epp(self):
  for p in self.hw.data['policies'].values():p['epp']='balance_performance'
 def test_change_does_not_lock_sliders(self):
  self.e.apply('default',self.values);self.change_epp();self.e.monitor()
  self.assertEqual(self.e.state,'active');self.e.apply('default',self.values)
 def test_legacy_journal_restores_turbo_and_limits_preserving_epp(self):
  self.e.apply('default',self.values);self.e.set_turbo(False)
  for p in self.e.tx['guards']['policies'].values():p['epp']='performance'
  self.e.persist();self.change_epp()
  recovered=d.Engine(self.hw,self.tmp.name,'boot',lambda:False)
  self.assertIsNone(recovered.tx);self.assertEqual(recovered.state,'idle')
  self.assertEqual(self.hw.data['global']['no_turbo'],'0')
  for n,p in self.hw.data['policies'].items():
   self.assertEqual(p['max'],self.original['policies'][n]['max']);self.assertEqual(p['epp'],'balance_performance')
 def test_epp_change_during_turbo_write_allowed(self):
  original=self.hw.write_no_turbo
  def write(value):original(value);self.change_epp()
  self.hw.write_no_turbo=write;self.e.set_turbo(False);self.e.restore()
  self.assertEqual(self.hw.data['global']['no_turbo'],'0')
 def test_real_limit_change_still_conflicts(self):
  self.e.apply('default',self.values);self.change_epp();self.hw.data['policies']['policy0']['max']=1800000
  self.e.monitor();self.assertEqual(self.e.state,'conflict')
 def test_governor_change_still_conflicts(self):
  self.e.apply('default',self.values);self.change_epp();self.hw.data['policies']['policy0']['governor']='performance'
  self.e.monitor();self.assertEqual(self.e.state,'conflict')
 def test_turbo_change_still_conflicts(self):
  self.e.apply('default',self.values);self.hw.write_no_turbo('1');self.e.monitor()
  self.assertEqual(self.e.state,'conflict')
