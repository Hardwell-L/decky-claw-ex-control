#!/usr/bin/env python3
"""Local status / safe restore utility; talks only to the fixed daemon socket."""
import json
import socket
import sys
import uuid

def call(req):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(30)
        s.connect('/run/claw-cpu-control/control.sock')
        s.sendall((json.dumps(req)+'\n').encode())
        with s.makefile('rb') as f:
            result=json.loads(f.readline(65536))
        if not result.get('ok'):
            raise RuntimeError(result.get('error','Request failed'))
        return result

if __name__ == '__main__':
    actions={'status':('get_status','restore'),'fan-status':('fan_status','fan_restore'),'power-status':('power_status','power_restore')}
    arg=sys.argv[1] if len(sys.argv)==2 else ''
    lookup={'restore':'status','fan-restore':'fan-status','power-restore':'power-status'}.get(arg,arg)
    if lookup not in actions: sys.exit('Usage: control.py status|restore|fan-status|fan-restore|power-status|power-restore')
    read,restore=actions[lookup]
    result=call({'method':read})
    if arg!=lookup: result=call({'method':restore,'request_id':uuid.uuid4().hex,'expected_revision':result['revision']})
    print(json.dumps(result,ensure_ascii=False,indent=2))
