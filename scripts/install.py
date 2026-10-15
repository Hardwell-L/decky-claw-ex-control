#!/usr/bin/env python3
"""Explicit local install; never invoked by packaging/building."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
LIB=Path('/usr/local/lib/claw-cpu-control')
UNIT=Path('/etc/systemd/system/claw-cpu-control.service')
PLUGIN=Path('/home/deck/homebrew/plugins/Claw EX Control')
STATE=Path('/var/lib/claw-cpu-control')

def safe_parent(path):
    for p in [path,*path.parents]:
        if p.is_symlink():
            raise RuntimeError('拒绝符号链接：'+str(p))

def run(*args): subprocess.run(args,check=True)

def main():
    if os.geteuid()!=0: sys.exit('请使用 sudo python3 scripts/install.py；本脚本会安装并启动只读待机服务。')
    account=pwd.getpwnam('deck')
    if not (PLUGIN.parent).is_dir(): sys.exit('未找到 /home/deck/homebrew/plugins，请先确认 Decky Loader 安装路径。')
    if Path('/home/deck/homebrew/plugins/Claw CPU Control').exists():
        sys.exit('检测到旧版 Claw CPU Control。请先恢复设置并卸载旧版，避免两个插件同时控制硬件。')
    for p in [LIB,UNIT,PLUGIN,STATE]: safe_parent(p)
    for p in [LIB,UNIT,PLUGIN,STATE]:
        if p.exists(): sys.exit('已有安装或恢复记录，请先检查，拒绝覆盖：'+str(p))
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for rel,expected in manifest.items():
        path=ROOT/rel
        if not path.resolve().is_relative_to(ROOT): raise RuntimeError('非法文件路径')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise RuntimeError('校验失败：'+rel)
    # Probe before installing anything. Does not write sysfs.
    probe=subprocess.check_output(['/usr/bin/python3',str(ROOT/'backend/daemon.py'),'--inspect'],text=True)
    LIB.mkdir(mode=0o755,parents=True)
    for name in ['daemon.py', 'fan.py']:
        shutil.copyfile(ROOT/'backend'/name,LIB/name); os.chmod(LIB/name,0o644)
    shutil.copyfile(ROOT/'scripts/control.py',LIB/'control.py'); os.chmod(LIB/'control.py',0o644)
    STATE.mkdir(mode=0o700)
    (STATE/'installation-snapshot.json').write_text(probe)
    os.chmod(STATE/'installation-snapshot.json',0o600)
    # Bind only this machine's verified MSI hwmon directory into the service sandbox.
    sys.path.insert(0, str(ROOT/'backend'))
    from fan import FanHardware
    fan_path = FanHardware().directory().resolve()
    unit_text = (ROOT/'systemd/claw-cpu-control.service').read_text()
    unit_text = unit_text.replace('[Install]', '[Service]\nReadWritePaths=' + str(fan_path.parent) + '\n\n[Install]')
    UNIT.write_text(unit_text); os.chmod(UNIT,0o644)
    shutil.copytree(ROOT/'decky-plugin',PLUGIN)
    for p in [PLUGIN,*PLUGIN.rglob('*')]:
        os.chown(p,account.pw_uid,account.pw_gid)
        os.chmod(p,0o755 if p.is_dir() else 0o644)
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now','claw-cpu-control.service')
    print('已安装。服务启动时不应用预设。请在 Decky 中重新载入插件或重启 Decky Loader 后检查状态。')

if __name__=='__main__': main()
