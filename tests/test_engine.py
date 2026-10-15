import copy
import importlib.util
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('daemon', Path(__file__).parents[1]/'backend/daemon.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class Hardware:
    def __init__(self):
        self.data = {'global': {'status':'active','no_turbo':'0'}, 'policies': {}}
        for i in range(10):
            maxfreq = [4600000,4700000][i] if i<2 else 3400000
            self.data['policies']['policy'+str(i)] = dict(group='P' if i<2 else 'E', cpus=[i], path='/mock/policy'+str(i), min=400000, max=maxfreq, hw_min=400000, hw_max=maxfreq, base=2200000 if i<2 else 1600000, governor='powersave', epp='performance')
        self.writes = []
        self.fail_once = None
        self.resume = {}
        self.turbo_hw = {n:p['hw_max'] for n,p in self.data['policies'].items()}
    def write_no_turbo(self, value):
        if value == '1':
            self.resume = {n:p['max'] for n,p in self.data['policies'].items()}
            for p in self.data['policies'].values():
                p['max']=min(p['max'],p['base']);p['hw_max']=p['base']
        else:
            for n,p in self.data['policies'].items():
                p['max']=self.resume.get(n,p['max']);p['hw_max']=self.turbo_hw[n]
        self.data['global']['no_turbo']=value
    def discover(self): return copy.deepcopy(self.data)
    def write_max(self, n, v):
        if n == self.fail_once:
            self.fail_once = None
            raise OSError('injected write failure')
        self.writes.append((n,v))
        self.data['policies'][n]['max'] = v
        if self.data['global']['no_turbo']=='1':self.resume[n]=v

class DelayedHardware(Hardware):
    def __init__(self):
        super().__init__()
        self.pending = None
    def write_max(self, n, v):
        self.writes.append((n, v))
        self.pending = [n, v, 3]
    def discover(self):
        if self.pending:
            self.pending[2] -= 1
            if self.pending[2] == 0:
                n, v, _ = self.pending
                self.data['policies'][n]['max'] = v
                self.pending = None
        return super().discover()

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hw = Hardware()
        self.original = self.hw.discover()
        self.gaming = True
        self.engine = d.Engine(self.hw,self.tmp.name,'boot-a',lambda:self.gaming)
        self.values = {'p_max_khz':3000000,'e_max_khz':2400000}
    def apply(self): self.engine.apply('balanced',self.values)
    def request(self, method, **kw):
        return self.engine.request({'method':method,'request_id':str(time.time_ns()),'expected_revision':self.engine.revision,**kw},1000)
    def test_delayed_sysfs_readback_applies_both_groups_and_restores(self):
        self.hw = DelayedHardware()
        self.engine.hw = self.hw
        self.apply()
        self.assertEqual(self.engine.state, 'active')
        for p in self.hw.data['policies'].values():
            self.assertEqual(p['max'], 3000000 if p['group']=='P' else 2400000)
        self.engine.restore()
        self.assertEqual(self.hw.data, self.original)
    def test_write_timeout_is_not_success(self):
        def ignore_write(n,v): self.hw.writes.append((n,v))
        self.hw.write_max = ignore_write
        with patch.object(d, 'WRITE_TIMEOUT', 0):
            with self.assertRaises(TimeoutError): self.apply()
        self.assertEqual(self.engine.state, 'idle')
        self.assertEqual(self.hw.data, self.original)
        self.assertEqual(self.hw.writes[-1], ('policy0',4600000))
    def test_unexpected_readback_not_claimed(self):
        def external_write(n,v): self.hw.data['policies'][n]['max']=2200000
        self.hw.write_max = external_write
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.engine.state, 'recovery_required')
        self.assertNotEqual(self.engine.tx['last']['policy0'],2200000)
    def test_old_settings_migrate_without_writes(self):
        Path(self.tmp.name,'presets.json').write_text(json.dumps({'schema':1,'presets':{'balanced':self.values,'powersave':None}}))
        engine=d.Engine(self.hw,self.tmp.name,'boot-a',lambda:True)
        self.assertEqual(engine.presets['balanced'],self.values)
        self.assertIsNone(engine.presets['default'])
        self.assertEqual(engine.ui,{'name':'default','independent':False})
        self.assertEqual(self.hw.writes,[])
    def test_default_auto_configure_saves_applies_and_restores(self):
        self.engine.request({'method':'heartbeat','session_token':'b'*32},1000)
        result=self.request('configure',name='default',independent=True,**self.values)
        self.assertTrue(result['ok'])
        self.assertEqual(self.engine.state,'active')
        self.assertEqual(self.engine.presets['default'],self.values)
        self.assertEqual(self.engine.ui,{'name':'default','independent':True})
        self.engine.restore()
        self.assertEqual(self.hw.data,self.original)
    def test_desktop_auto_configure_saves_without_applying(self):
        self.gaming=False
        result=self.request('configure',name='default',independent=False,**self.values)
        self.assertTrue(result['ok'])
        self.assertEqual(self.hw.writes,[])
        self.assertEqual(self.engine.presets['default'],self.values)
    def test_ui_and_values_survive_restart_without_auto_apply(self):
        self.gaming=False
        self.request('configure',name='powersave',independent=True,**self.values)
        restarted=d.Engine(self.hw,self.tmp.name,'boot-b',lambda:True)
        self.assertEqual(restarted.status()['ui'],{'name':'powersave','independent':True})
        self.assertEqual(restarted.presets['powersave'],self.values)
        self.assertEqual(restarted.state,'idle')
        self.assertEqual(self.hw.writes,[])
    def test_only_selection_and_mode_save_no_hardware_writes(self):
        result=self.request('save_ui',name='balanced',independent=True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.engine.ui,{'name':'balanced','independent':True})
        self.assertEqual(self.hw.writes,[])
    def test_configure_failure_keeps_saved_values_but_rolls_back(self):
        self.engine.request({'method':'heartbeat','session_token':'b'*32},1000)
        self.hw.fail_once='policy3'
        result=self.request('configure',name='default',independent=True,**self.values)
        self.assertFalse(result['ok'])
        self.assertEqual(self.engine.presets['default'],self.values)
        self.assertEqual(self.hw.data,self.original)
    def test_invalid_ui_does_not_save_or_apply(self):
        result=self.request('configure',name='default',independent='yes',**self.values)
        self.assertFalse(result['ok'])
        self.assertEqual(self.hw.writes,[])
        self.assertFalse(self.engine.presets_file.exists())
    def test_turbo_off_and_restore_preserves_all_limits(self):
        self.engine.last_heartbeat=time.monotonic()
        self.engine.set_turbo(False)
        self.assertFalse(self.engine.status()['turbo_enabled'])
        self.assertEqual(self.engine.state,'active')
        self.engine.restore()
        self.assertEqual(self.hw.data,self.original)
    def test_turbo_and_frequency_limits_coexist(self):
        self.engine.last_heartbeat=time.monotonic()
        self.apply()
        self.engine.set_turbo(False)
        self.engine.monitor()
        self.assertEqual(self.engine.state,'active')
        self.engine.apply('powersave',{'p_max_khz':2500000,'e_max_khz':1800000})
        self.assertEqual(self.hw.data['policies']['policy0']['max'],2200000)
        self.engine.set_turbo(True)
        self.assertEqual(self.hw.data['policies']['policy0']['max'],2500000)
        self.assertEqual(self.hw.data['policies']['policy2']['max'],1800000)
        self.engine.restore()
        self.assertEqual(self.hw.data,self.original)
    def test_turbo_same_boot_recovery(self):
        self.engine.last_heartbeat=time.monotonic()
        self.engine.set_turbo(False)
        d.Engine(self.hw,self.tmp.name,'boot-a',lambda:True)
        self.assertEqual(self.hw.data,self.original)
    def test_turbo_cross_boot_no_writes(self):
        self.engine.last_heartbeat=time.monotonic()
        self.engine.set_turbo(False)
        before=self.hw.discover()
        d.Engine(self.hw,self.tmp.name,'boot-b',lambda:True)
        self.assertEqual(self.hw.data,before)
    def test_turbo_pending_crash_recovery(self):
        snap=self.hw.discover();self.engine.takeover(snap)
        self.engine.tx['turbo']={'baseline':'0','last':'0','pending':{'target':'1','before':snap}}
        self.engine.persist();self.hw.write_no_turbo('1')
        d.Engine(self.hw,self.tmp.name,'boot-a',lambda:True)
        self.assertEqual(self.hw.data,self.original)
    def test_turbo_external_change_preserved(self):
        self.engine.last_heartbeat=time.monotonic();self.engine.set_turbo(False)
        self.hw.write_no_turbo('0');before=self.hw.discover()
        self.engine.restore()
        self.assertEqual(self.hw.data,before)
    def test_turbo_desktop_and_invalid_input_rejected(self):
        self.engine.last_heartbeat=time.monotonic()
        with self.assertRaises(ValueError):self.engine.set_turbo(1)
        self.gaming=False
        with self.assertRaises(ValueError):self.engine.set_turbo(False)
        self.assertEqual(self.hw.data,self.original)
    def test_turbo_initially_off_restores_off(self):
        self.hw.write_no_turbo('1');before=self.hw.discover()
        self.engine.last_heartbeat=time.monotonic();self.engine.set_turbo(True)
        self.engine.restore();self.assertEqual(self.hw.data,before)
    def test_idle_boot_does_not_write(self):
        self.engine.status()
        self.assertEqual(self.hw.writes,[])
    def test_restore_preserves_distinct_p_limits_and_other_controls(self):
        self.apply(); self.engine.restore()
        self.assertEqual(self.hw.data,self.original)
        self.assertFalse(self.engine.tx)
    def test_switch_preserves_original_baseline(self):
        self.apply()
        self.engine.apply('powersave',{'p_max_khz':2400000,'e_max_khz':1800000})
        self.engine.restore()
        self.assertEqual(self.hw.data,self.original)
    def test_save_never_writes_hardware(self):
        self.assertTrue(self.request('save_preset',name='balanced',**self.values)['ok'])
        self.assertEqual(self.hw.writes,[])
    def test_failure_rolls_back_all_previous_writes(self):
        self.hw.fail_once='policy4'
        with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.hw.data,self.original)
        self.assertIsNone(self.engine.tx)
    def test_second_apply_failure_restores_first_preset(self):
        self.apply(); first=self.hw.discover()
        self.hw.fail_once='policy5'
        with self.assertRaises(OSError): self.engine.apply('powersave',{'p_max_khz':2400000,'e_max_khz':1800000})
        self.assertEqual(self.hw.data,first)
        self.assertEqual(self.engine.state,'active')
    def test_external_owner_not_overwritten(self):
        self.apply()
        self.hw.data['policies']['policy0']['max']=2500000
        self.engine.monitor()
        self.assertEqual(self.engine.state,'conflict')
        self.engine.restore()
        self.assertEqual(self.hw.data['policies']['policy0']['max'],2500000)
        self.assertEqual(self.hw.data['policies']['policy1']['max'],4700000)
    def test_governor_change_blocks_further_apply(self):
        self.apply(); self.hw.data['policies']['policy0']['governor']='performance'
        self.engine.monitor()
        with self.assertRaises(ValueError): self.apply()
        self.engine.restore()
        self.assertEqual(self.hw.data['policies']['policy0']['governor'],'performance')
    def test_same_boot_restart_restores(self):
        self.apply()
        d.Engine(self.hw,self.tmp.name,'boot-a',lambda:True)
        self.assertEqual(self.hw.data,self.original)
    def test_new_boot_never_restores_stale_values(self):
        self.apply(); self.hw.writes.clear()
        d.Engine(self.hw,self.tmp.name,'boot-b',lambda:True)
        self.assertEqual(self.hw.writes,[])
    def test_pending_crash_write_recovered(self):
        self.apply()
        self.engine.tx['pending']={'policy':'policy0','previous':3000000,'target':2000000}
        self.engine.persist(); self.hw.data['policies']['policy0']['max']=2000000
        d.Engine(self.hw,self.tmp.name,'boot-a',lambda:True)
        self.assertEqual(self.hw.data,self.original)
    def test_topology_change_preserves_journal_without_writes(self):
        self.apply(); self.hw.writes.clear()
        self.hw.data['policies']['policy0']['cpus']=[11]
        with self.assertRaises(ValueError): self.engine.restore()
        self.assertEqual(self.hw.writes,[])
        self.assertTrue(self.engine.journal.exists())
    def test_invalid_values_write_nothing(self):
        for value in [True,-1,200000,4800000,1.2,'3000000']:
            with self.assertRaises(ValueError): self.engine.apply('balanced',{**self.values,'p_max_khz':value})
        self.assertEqual(self.hw.writes,[])
    def test_group_ceiling_clamps_individual_p(self):
        self.engine.apply('balanced',{**self.values,'p_max_khz':4700000})
        self.assertEqual(self.hw.data['policies']['policy0']['max'],4600000)
        self.assertEqual(self.hw.data['policies']['policy1']['max'],4700000)
    def test_desktop_cannot_apply(self):
        self.gaming=False
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.hw.writes,[])
    def test_heartbeat_required(self):
        result=self.request('apply',name='balanced',**self.values)
        self.assertFalse(result['ok']); self.assertEqual(self.hw.writes,[])
    def test_retry_idempotence_and_revision(self):
        self.engine.request({'method':'heartbeat','session_token':'a'*32},1000)
        req={'method':'apply','request_id':'fixed-id-1234','expected_revision':self.engine.revision,'name':'balanced',**self.values}
        result=self.engine.request(req,1000)
        count=len(self.hw.writes)
        self.assertTrue(result['ok'])
        self.assertEqual(self.engine.request(req,1000),result)
        self.assertEqual(len(self.hw.writes),count)
        with self.assertRaises(ValueError): self.engine.request({**req,'request_id':'new-id-1234'},1000)
        with self.assertRaises(ValueError): self.engine.request({**req,'p_max_khz':2000000},1000)
    def test_heartbeat_loss_restores(self):
        self.apply(); self.engine.last_heartbeat=100
        self.engine.watchdog(159)
        self.assertIsNotNone(self.engine.tx)
        self.engine.watchdog(161)
        self.assertEqual(self.hw.data,self.original)
    def test_leaving_game_session_restores(self):
        self.apply(); self.engine.last_heartbeat=100
        self.gaming=False
        self.engine.watchdog(101)
        self.assertEqual(self.hw.data,self.original)
    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError): self.engine.request({'method':'get_status','path':'/etc/passwd'},1000)
    def test_restore_failure_retains_journal_then_retry(self):
        self.apply(); self.hw.fail_once='policy3'
        with self.assertRaises(ValueError): self.engine.restore()
        self.assertEqual(self.engine.state,'recovery_required')
        self.assertTrue(self.engine.journal.exists())
        self.engine.restore(); self.assertEqual(self.hw.data,self.original)

if __name__=='__main__': unittest.main()
