#!/usr/bin/env python3
"""Restore before uninstall. Keep snapshots/journal/history for audit."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from control import call

if __name__=='__main__':
    if os.geteuid()!=0: sys.exit('Run sudo python3 scripts/uninstall.py')
    # If daemon is unavailable, stop here. Do not erase recovery evidence.
    import uuid
    power=call({'method':'power_status'})
    if power.get('has_transaction'):
        power=call({'method':'power_restore','request_id':uuid.uuid4().hex,'expected_revision':power['revision']})
        if power.get('has_transaction'): sys.exit('Power recovery incomplete; installation and records retained.')
    fan=call({'method':'fan_status'})
    if fan.get('has_transaction'):
        fan=call({'method':'fan_restore','request_id':uuid.uuid4().hex,'expected_revision':fan['revision']})
        if fan.get('has_transaction'): sys.exit('Fan recovery incomplete; installation and records retained.')
    result=call({'method':'get_status'})
    result=call({'method':'restore','request_id':uuid.uuid4().hex,'expected_revision':result['revision']})
    if result.get('has_transaction'): sys.exit('Recovery incomplete; installation and records retained.')
    subprocess.run(['systemctl','disable','--now','claw-cpu-control.service'],check=True)
    paths=[Path('/usr/local/lib/claw-cpu-control'),Path('/etc/systemd/system/claw-cpu-control.service'),Path('/home/deck/homebrew/plugins/Claw EX Control')]
    for p in paths:
        if any(x.is_symlink() for x in [p,*p.parents]): sys.exit('Refusing to remove a symlink path: '+str(p))
    for p in paths:
        if p.is_dir(): shutil.rmtree(p)
        elif p.exists(): p.unlink()
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Plugin uninstalled; presets and recovery history retained in /var/lib/claw-cpu-control.')
