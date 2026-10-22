"""Recover an EPP-only conflict using the fixed daemon, retaining evidence."""
import hashlib,importlib.util,json,os,shutil,socket,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(__file__).resolve().parents[1]
lib=Path('/usr/local/lib/claw-cpu-control');state=Path('/var/lib/claw-cpu-control')
plugin=Path('/home/deck/homebrew/plugins/Claw CPU Control')
if os.geteuid()!=0:raise SystemExit('System authentication required')
def rpc(method):
 with socket.socket(socket.AF_UNIX) as s:
  s.settimeout(10);s.connect('/run/claw-cpu-control/control.sock');s.sendall((json.dumps({'method':method})+'\n').encode())
  with s.makefile() as f:return json.loads(f.readline())
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout
for path in [lib,state,plugin,lib/'daemon.py',plugin/'package.json']:
 if any(p.is_symlink() for p in [path,*path.parents]):raise SystemExit('Refusing symlink')
for method in ['fan_status','power_status']:
 r=rpc(method)
 if not r.get('ok') or r.get('has_transaction'):raise SystemExit('Fan or power control is active; not installed')
spec=importlib.util.spec_from_file_location('fixed',root/'backend/daemon.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
snap=d.Hardware().discover();tx=json.loads((state/'transaction.json').read_text()) if (state/'transaction.json').exists() else None
if tx:
 guard=json.loads(json.dumps(tx['guards']))
 for p in guard['policies'].values():p.pop('epp',None)
 if guard!=d.guards(snap) or tx['topology']!=d.fingerprint(snap) or any(snap['policies'][n]['max']!=v for n,v in tx['last'].items()) or tx.get('pending') or tx.get('turbo',{}).get('pending'):
  raise SystemExit('Non-EPP conflict detected; installation and recovery records unchanged')
backup=state/('epp-fix-'+str(time.time_ns()));backup.mkdir(mode=0o700)
shutil.copy2(lib/'daemon.py',backup/'daemon.py');shutil.copy2(plugin/'package.json',backup/'package.json')
for p in state.glob('*.json'):shutil.copy2(p,backup/p.name)
(backup/'snapshot.json').write_text(json.dumps(snap,indent=2))
print('Backup completed: '+str(backup),flush=True)
try:
 run('systemctl','stop','plugin_loader.service')
 run('systemctl','stop','claw-cpu-control.service')
 target=lib/'daemon.py';tmp=target.with_suffix('.new');tmp.write_bytes((root/'backend/daemon.py').read_bytes());os.chown(tmp,0,0);os.chmod(tmp,0o644);tmp.replace(target)
 run('systemctl','start','claw-cpu-control.service')
 for _ in range(80):
  try:
   r=rpc('get_status')
   if r.get('ok'):break
  except (OSError,ValueError):pass
  time.sleep(.1)
 else:raise RuntimeError('Service is not ready')
 if r.get('has_transaction') or r['state']!='idle':raise RuntimeError('Recovery issue remains: '+str(r.get('message')))
 if tx:
  assert all(r['policies'][n]['max']==v for n,v in tx['baseline'].items())
  if tx.get('turbo'):assert r['global']['no_turbo']==tx['turbo']['baseline']
 assert all(r['policies'][n]['epp']==p['epp'] for n,p in snap['policies'].items()),'EPP changed'
 assert (lib/'daemon.py').read_bytes()==(root/'backend/daemon.py').read_bytes()
 p=plugin/'package.json';info=json.loads(p.read_text());info['version']='0.6.2';p.write_text(json.dumps(info,indent=2)+'\n')
 (backup/'verification.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
 print(json.dumps({'state':r['state'],'turbo_enabled':r['turbo_enabled'],'limits_mhz':sorted(set(p['max']//1000 for p in r['policies'].values())),'epp_preserved':True}),flush=True)
finally:
 run('systemctl','start','claw-cpu-control.service')
 run('systemctl','start','plugin_loader.service')
