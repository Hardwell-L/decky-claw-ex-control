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
    if sys.argv[1:] not in (['status'], ['restore'], ['fan-status'], ['fan-restore']):
        sys.exit('Usage: control.py status|restore|fan-status|fan-restore')
    fan=sys.argv[1].startswith('fan-')
    result=call({'method':'fan_status' if fan else 'get_status'})
    if sys.argv[1] in ('restore','fan-restore'):
        result=call({'method':'fan_restore' if fan else 'restore','request_id':uuid.uuid4().hex,'expected_revision':result['revision']})
    print(json.dumps(result,ensure_ascii=False,indent=2))
