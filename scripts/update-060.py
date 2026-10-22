#!/usr/bin/env python3
"""Backed-up upgrade; validation is read-only. Run through pkexec."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]/'release/decky-claw-ex-control'
LIB=Path('/usr/local/lib/claw-cpu-control')
PLUGIN=Path('/home/deck/homebrew/plugins/Claw CPU Control')
STATE=Path('/var/lib/claw-cpu-control')
UNIT=Path('/etc/systemd/system/claw-cpu-control.service')

def run(*args): return subprocess.run(args,check=True,capture_output=True,text=True).stdout

def status(kind):
    return json.loads(run('/usr/bin/python3',str(LIB/'control.py'),kind))

def main():
    if os.geteuid()!=0:raise RuntimeError('System authorization required')
    sys.dont_write_bytecode=True
    if json.loads((ROOT/'decky-plugin/package.json').read_text())['version']!='0.6.0':raise RuntimeError('Package version mismatch')
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for name,digest in manifest.items():
        path=ROOT/name
        if not path.resolve().is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Package verification failed: '+name)
    for path in [LIB,PLUGIN,STATE,UNIT]:
        if any(p.is_symlink() for p in [path,*path.parents]):raise RuntimeError('Refusing symlink')
    sys.path.insert(0,str(ROOT/'backend'))
    from fan import FanHardware
    hw=FanHardware();before=hw.snapshot();hw.telemetry()
    if before['pwm1_enable']!=2 or before['pwm2_enable']!=2:raise RuntimeError('Restore automatic fan mode first')
    from power import PowerHardware
    power_hw=PowerHardware();power_before=power_hw.snapshot()
    prior=status('status')
    if prior['has_transaction']:raise RuntimeError('CPU control is active; restore before upgrading')
    for name in ['transaction.json','fan-transaction.json','power-transaction.json']:
        if (STATE/name).exists():raise RuntimeError('Pending recovery record: '+name)
    unit=(ROOT/'systemd/claw-cpu-control.service').read_text()
    fan_parent=hw.directory().resolve().parent
    if not str(fan_parent).startswith('/sys/devices/') or fan_parent.name!='hwmon':raise RuntimeError('Unexpected hwmon path')
    unit=unit.replace('[Install]','[Service]\nReadWritePaths='+str(fan_parent)+'\n\n[Install]')
    for name in PowerHardware.names.values():
        power_path=(PowerHardware.root/name/'current_value').resolve()
        if not str(power_path).startswith('/sys/devices/'):raise RuntimeError('Unexpected power path')
        unit=unit.replace('[Install]','[Service]\nReadWritePaths='+str(power_path)+'\n\n[Install]')
    backup=STATE/('upgrade-060-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    shutil.copytree(LIB,backup/'lib');shutil.copytree(PLUGIN,backup/'plugin');shutil.copy2(UNIT,backup/'unit')
    (backup/'before.json').write_text(json.dumps({'fan':before,'cpu':prior},indent=2))
    for name in ['presets.json','fan-presets.json','power-preset.json','autostart.json']:
        if (STATE/name).exists():shutil.copy2(STATE/name,backup/name)
    print('Backup completed: '+str(backup),flush=True)
    stopped=False
    try:
        run('systemctl','stop','plugin_loader.service');stopped=True
        run('systemctl','stop','claw-cpu-control.service')
        for name in ['transaction.json','fan-transaction.json','power-transaction.json']:
            if (STATE/name).exists():raise RuntimeError('Recovery record remains after stop; refusing overwrite')
        for name in ['daemon.py','fan.py','power.py','autostart.py']:
            shutil.copyfile(ROOT/'backend'/name,LIB/name);os.chmod(LIB/name,0o644)
        shutil.copyfile(ROOT/'scripts/control.py',LIB/'control.py');os.chmod(LIB/'control.py',0o644)
        UNIT.write_text(unit);os.chmod(UNIT,0o644)
        account=pwd.getpwnam('deck')
        for source in (ROOT/'decky-plugin').rglob('*'):
            target=PLUGIN/source.relative_to(ROOT/'decky-plugin')
            if target.is_symlink():raise RuntimeError('Plugin path contains a symlink')
            if source.is_dir():target.mkdir(exist_ok=True)
            else:shutil.copyfile(source,target)
            os.chown(target,account.pw_uid,account.pw_gid);os.chmod(target,0o755 if source.is_dir() else 0o644)
        run('systemctl','daemon-reload');run('systemctl','start','claw-cpu-control.service')
        for _ in range(50):
            try:
                cpu=status('status');fan=status('fan-status');break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('New service is not ready')
        power=status('power-status')
        assert power['ok'] and not power['has_transaction']
        assert power_hw.snapshot()==power_before,'Power settings changed'
        assert not cpu['has_transaction'] and not fan['has_transaction']
        assert hw.snapshot()==before,'Fan settings changed'
        assert cpu['policies']==prior['policies'] and cpu['global']==prior['global'],'CPU settings changed'
        assert cpu['presets']==prior['presets'] and cpu['ui']==prior['ui'],'Original presets were not retained'
        (backup/'verification.json').write_text(json.dumps({'cpu':cpu,'fan':fan,'fan_config_unchanged':True},ensure_ascii=False,indent=2))
        print('0.6.0 Backend and fan read-only checks passed; original CPU presets retained.',flush=True)
    except Exception:
        run('systemctl','stop','claw-cpu-control.service')
        if not any((STATE/n).exists() for n in ['transaction.json','fan-transaction.json','power-transaction.json']):
            shutil.copytree(backup/'lib',LIB,dirs_exist_ok=True)
            shutil.copytree(backup/'plugin',PLUGIN,dirs_exist_ok=True)
            shutil.copy2(backup/'unit',UNIT)
            run('systemctl','daemon-reload');run('systemctl','start','claw-cpu-control.service')
            print('Upgrade failed; original files restored.',flush=True)
        raise
    finally:
        if stopped:run('systemctl','start','plugin_loader.service')
    print('Installation completed; backup: '+str(backup),flush=True)

if __name__=='__main__':main()
