import copy
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from test_engine import d
spec = importlib.util.spec_from_file_location('fan', Path(__file__).parents[1]/'backend/fan.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

class HW:
    def __init__(self):
        self.data = {'pwm1_enable': 2, 'pwm2_enable': 2}
        for fan in (1, 2):
            for i, (temp, pwm) in enumerate(zip([47,50,57,64,71,78],[60,64,68,74,80,84]), 1):
                self.data[f'pwm{fan}_auto_point{i}_temp'] = temp
                self.data[f'pwm{fan}_auto_point{i}_pwm'] = (pwm + fan - 1)*255//100
        self.original = copy.deepcopy(self.data)
        self.writes = []
        self.fail = None
    def snapshot(self): return copy.deepcopy(self.data)
    def telemetry(self): return {'rpm':[4000,4100], 'temperature':57}
    def write(self, key, value):
        if key == self.fail:
            self.fail = None
            raise OSError('injected')
        self.writes.append((key,value))
        if key == 'pwm1_enable':
            if value == 2:
                self.data = copy.deepcopy(self.original)
                self.data['pwm1_auto_point1_pwm'] = 70*255//100
            self.data['pwm1_enable'] = self.data['pwm2_enable'] = value
        else:self.data[key] = value*100//255*255//100 if key.endswith('_pwm') else value

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.hw=HW();self.game=True;self.beat=time.monotonic()
        self.e=self.engine()
        self.draft={'mode':'curve','fixed':100,'points':[{'temp':t,'output':100} for t in [47,50,57,64,71,78]]}
    def engine(self, boot='boot'):
        return f.FanEngine(self.hw,self.tmp.name,d.atomic_json,lambda:self.game,lambda:self.beat,boot)
    def request(self, method, **kwargs):
        import uuid
        return self.e.request(dict(method=method,request_id=uuid.uuid4().hex,expected_revision=self.e.revision,**kwargs))
    def test_pwm_all_percent_roundtrip(self):
        for p in range(101):self.assertEqual(f.encode(f.decode(p*255//100))*100//255,p)
    def test_save_does_not_write(self):
        self.assertTrue(self.request('fan_save',slot=2,draft=self.draft)['ok'])
        self.assertFalse(self.hw.writes)
        self.assertEqual(self.engine().settings['selected'],2)
    def test_dual_apply_restore_exact_distinct_originals(self):
        self.e.apply(1,self.draft)
        self.assertEqual(self.e.state,'active')
        for fan in (1,2):
            for i in range(1,7):self.assertEqual(self.hw.data[f'pwm{fan}_auto_point{i}_pwm'],255)
        self.e.restore();self.assertEqual(self.hw.data,self.hw.original)
        self.assertFalse(self.e.journal.exists())
    def test_partial_failure_rolls_back(self):
        self.hw.fail='pwm2_auto_point3_pwm'
        with self.assertRaises(OSError):self.e.apply(1,self.draft)
        self.assertEqual(self.hw.data,self.hw.original)
    def test_lower_output_applies_both_and_restores(self):
        self.draft['mode']='fixed';self.draft['fixed']=20
        result=self.request('fan_apply',slot=3,draft=self.draft)
        self.assertTrue(result['ok'])
        for fan in (1,2):
            for i in range(1,7):self.assertEqual(self.hw.data[f'pwm{fan}_auto_point{i}_pwm'],51)
        self.e.restore();self.assertEqual(self.hw.data,self.hw.original)
    def test_out_of_order_rejected(self):
        self.draft['points'][1]['temp']=40
        self.assertFalse(self.request('fan_apply',slot=1,draft=self.draft)['ok'])
        self.assertFalse(self.hw.writes)
    def test_stale_revision_rejected(self):
        with self.assertRaises(ValueError):self.e.request({'method':'fan_restore','expected_revision':'old','request_id':'abcdefgh'})
        self.assertFalse(self.hw.writes)
    def test_external_changes_not_overwritten(self):
        self.e.apply(1,self.draft);self.hw.data['pwm1_auto_point1_pwm']=200
        before=self.hw.snapshot()
        with self.assertRaises(ValueError):self.e.restore()
        self.assertEqual(before,self.hw.snapshot());self.assertTrue(self.e.journal.exists())
    def test_restart_and_reboot_restore_without_reapply(self):
        for boot in ['boot','new-boot']:
            self.e.apply(1,self.draft)
            self.e=self.engine(boot)
            self.assertEqual(self.hw.data,self.hw.original);self.assertIsNone(self.e.tx)
    def test_heartbeat_loss_restores(self):
        self.e.apply(1,self.draft);self.beat-=61;self.e.watchdog()
        self.assertEqual(self.hw.data,self.hw.original)
    def test_desktop_rejects_application(self):
        self.game=False
        with self.assertRaises(ValueError):self.e.apply(1,self.draft)
        self.assertFalse(self.hw.writes)
    def test_foreign_custom_mode_refused(self):
        self.hw.data['pwm1_enable']=self.hw.data['pwm2_enable']=1
        with self.assertRaises(ValueError):self.e.apply(1,self.draft)
        self.assertFalse(self.hw.writes)
    def test_recovery_failure_preserves_journal(self):
        self.e.apply(1,self.draft);self.hw.fail='pwm1_enable'
        with self.assertRaises(OSError):self.e.restore()
        self.assertTrue(self.e.journal.exists());self.e.restore()
        self.assertEqual(self.hw.data,self.hw.original)

    def test_missing_rpm_after_grace_restores(self):
        self.e.apply(1,self.draft);self.e.started-=11
        self.hw.telemetry=lambda:{'rpm':[4000,0],'temperature':57}
        self.e.watchdog();self.assertEqual(self.hw.data,self.hw.original)
        self.assertIn('RPM feedback',self.e.error)
    def test_read_failure_restores(self):
        self.e.apply(1,self.draft)
        def unavailable(): raise OSError('sensor missing')
        self.hw.telemetry=unavailable
        self.e.watchdog();self.assertEqual(self.hw.data,self.hw.original)

    def test_variable_curve_percent_roundtrip(self):
        for point,output in zip(self.draft['points'],[20,30,40,60,80,100]):point['output']=output
        self.e.apply(1,self.draft)
        for fan in (1,2):
            for i,point in enumerate(self.draft['points'],1):
                self.assertEqual(self.hw.data[f'pwm{fan}_auto_point{i}_pwm'],point['output']*255//100)
        self.e.restore();self.assertEqual(self.hw.data,self.hw.original)

    def test_boot_option_defaults_off(self):
        self.assertFalse(self.e.status()['autostart'])
        self.e.watchdog();self.assertFalse(self.hw.writes)
    def test_autostart_requires_successful_apply(self):
        self.assertFalse(self.request('fan_autostart',enabled=True)['ok'])
        self.assertFalse(self.hw.writes)
    def test_autostart_waits_for_game_and_heartbeat(self):
        self.e.apply(1,self.draft)
        self.assertTrue(self.request('fan_autostart',enabled=True)['ok'])
        self.e.restore();self.e=self.engine('new-boot');self.game=False
        self.e.watchdog();self.assertIsNone(self.e.tx)
        self.game=True;self.beat=0
        self.e.watchdog();self.assertIsNone(self.e.tx)
        self.beat=time.monotonic();self.e.watchdog();self.assertEqual(self.e.state,'active')
        self.e.restore();self.e.watchdog();self.assertIsNone(self.e.tx)
        self.e=self.engine('new-boot');self.e.watchdog();self.assertIsNone(self.e.tx)
    def test_autostart_uses_applied_not_draft(self):
        self.e.apply(1,self.draft);self.request('fan_autostart',enabled=True)
        altered=copy.deepcopy(self.draft);altered['fixed']=25;altered['mode']='fixed'
        self.request('fan_save',slot=2,draft=altered)
        self.e.restore();self.e=self.engine('new-boot');self.e.watchdog()
        self.assertEqual(self.e.tx['applied']['draft'],self.draft)
    def test_disable_autostart_keeps_current_fan(self):
        self.e.apply(1,self.draft);self.request('fan_autostart',enabled=True)
        before=self.hw.snapshot();self.request('fan_autostart',enabled=False)
        self.assertEqual(before,self.hw.snapshot())
        self.e.restore();self.e=self.engine('new-boot');self.e.watchdog();self.assertIsNone(self.e.tx)
    def test_boot_failure_not_retried(self):
        self.e.apply(1,self.draft);self.request('fan_autostart',enabled=True);self.e.restore()
        self.e=self.engine('new-boot');self.hw.fail='pwm2_auto_point3_pwm';self.e.watchdog()
        self.assertIsNone(self.e.tx);count=len(self.hw.writes)
        self.e.watchdog();self.assertEqual(len(self.hw.writes),count)
    def test_manual_restore_cancels_pending_boot(self):
        self.e.apply(1,self.draft);self.request('fan_autostart',enabled=True);self.e.restore()
        self.e=self.engine('new-boot');self.request('fan_restore');self.e.watchdog()
        self.assertIsNone(self.e.tx)

    def resume_ready(self, reset=True):
        self.e.on_resume()
        self.e.resume_at -= 3
        self.beat = time.monotonic()
        if reset:
            self.hw.data = copy.deepcopy(self.hw.original)

    def test_resume_reapplies_applied_not_saved_draft(self):
        self.e.apply(1, self.draft)
        baseline = copy.deepcopy(self.e.tx['baseline'])
        altered = copy.deepcopy(self.draft); altered['mode']='fixed'; altered['fixed']=20
        self.request('fan_save', slot=2, draft=altered)
        self.resume_ready()
        self.e.watchdog()
        self.assertEqual(self.e.state, 'active')
        self.assertEqual(self.e.tx['applied']['draft'], self.draft)
        self.assertEqual(self.e.tx['baseline'], baseline)
        self.e.restore(); self.assertEqual(self.hw.data, self.hw.original)

    def test_resume_unchanged_still_reactivates_and_grants_rpm_grace(self):
        self.e.apply(1, self.draft); self.resume_ready(reset=False)
        count=len(self.hw.writes)
        self.hw.telemetry=lambda:{'rpm':[0,0], 'temperature':57}
        self.e.watchdog(); self.e.watchdog()
        self.assertGreater(len(self.hw.writes), count)
        self.assertEqual(self.e.state, 'active')

    def test_resume_inactive_never_applies(self):
        self.e.on_resume(); self.e.watchdog()
        self.assertFalse(self.hw.writes)

    def test_manual_off_during_resume_cancels_reapply(self):
        self.e.apply(1, self.draft); self.resume_ready()
        self.request('fan_restore'); count=len(self.hw.writes)
        self.e.watchdog()
        self.assertIsNone(self.e.tx)
        self.assertEqual(self.hw.data, self.hw.original)
        self.assertEqual(len(self.hw.writes), count)

    def test_resume_foreign_custom_not_overwritten(self):
        self.e.apply(1, self.draft); self.resume_ready(reset=False)
        self.hw.data['pwm1_auto_point1_pwm']=51
        count=len(self.hw.writes); self.e.watchdog()
        self.assertEqual(self.e.state, 'conflict')
        self.assertEqual(len(self.hw.writes), count)

    def test_resume_waits_for_fresh_heartbeat_then_times_out(self):
        self.e.apply(1, self.draft); self.resume_ready()
        self.beat=self.e.resume_at-1
        count=len(self.hw.writes); self.e.watchdog()
        self.assertEqual(len(self.hw.writes), count)
        self.e.resume_at-=61; self.beat=self.e.resume_at-1
        self.e.watchdog()
        self.assertIsNone(self.e.tx)
        self.assertEqual(self.hw.data, self.hw.original)

    def test_resume_device_temporarily_missing(self):
        self.e.apply(1, self.draft); self.resume_ready()
        original=self.hw.snapshot
        def missing(): raise OSError('device missing')
        self.hw.snapshot=missing; self.e.watchdog()
        self.assertIsNotNone(self.e.resume_at)
        self.hw.snapshot=original; self.e.watchdog()
        self.assertEqual(self.e.state, 'active')

    def test_resume_partial_write_failure_restores(self):
        self.e.apply(1, self.draft); self.resume_ready()
        self.hw.fail='pwm2_auto_point3_pwm'; self.e.watchdog()
        self.assertIsNone(self.e.tx)
        self.assertEqual(self.hw.data, self.hw.original)
        self.assertIn('Application after resume failed', self.e.error)

    def test_resume_without_game_restores_after_grace(self):
        self.e.apply(1, self.draft); self.resume_ready(); self.game=False
        self.e.watchdog(); self.assertIsNotNone(self.e.resume_at)
        self.e.resume_at-=61; self.e.watchdog()
        self.assertIsNone(self.e.tx)
        self.assertEqual(self.hw.data, self.hw.original)

    def test_unchanged_curve_points_skip_writes(self):
        for point,output in zip(self.draft['points'],[60,64,68,74,80,84]):point['output']=output
        self.e.apply(1,self.draft)
        # Mode changes still run; unchanged temperatures are not rewritten.
        self.assertFalse(any(key.endswith('_temp') for key,value in self.hw.writes))
        self.assertIn(('pwm1_enable',1),self.hw.writes)
        self.e.restore();self.assertEqual(self.hw.data,self.hw.original)
