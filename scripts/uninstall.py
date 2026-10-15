#!/usr/bin/env python3
"""Restore before uninstall. Keep snapshots/journal/history for audit."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from control import call

if __name__=='__main__':
    if os.geteuid()!=0: sys.exit('需要 sudo python3 scripts/uninstall.py')
    # If daemon is unavailable, stop here. Do not erase recovery evidence.
    import uuid
    fan=call({'method':'fan_status'})
    if fan.get('has_transaction'):
        fan=call({'method':'fan_restore','request_id':uuid.uuid4().hex,'expected_revision':fan['revision']})
        if fan.get('has_transaction'): sys.exit('风扇恢复未完成，保留安装和记录。')
    result=call({'method':'get_status'})
    result=call({'method':'restore','request_id':uuid.uuid4().hex,'expected_revision':result['revision']})
    if result.get('has_transaction'): sys.exit('恢复未完成，保留安装和记录。')
    subprocess.run(['systemctl','disable','--now','claw-cpu-control.service'],check=True)
    paths=[Path('/usr/local/lib/claw-cpu-control'),Path('/etc/systemd/system/claw-cpu-control.service'),Path('/home/deck/homebrew/plugins/Claw EX Control')]
    for p in paths:
        if any(x.is_symlink() for x in [p,*p.parents]): sys.exit('拒绝删除符号链接路径：'+str(p))
    for p in paths:
        if p.is_dir(): shutil.rmtree(p)
        elif p.exists(): p.unlink()
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('插件已卸载；/var/lib/claw-cpu-control 中的预设和恢复历史已保留。')
