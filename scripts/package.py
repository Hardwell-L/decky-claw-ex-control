#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

root=Path(__file__).resolve().parents[1]
version=json.loads((root/'package.json').read_text())['version']
release=root/'release'
release.mkdir(exist_ok=True)
stage=release/'decky-claw-ex-control'
if stage.exists(): shutil.rmtree(stage)
stage.mkdir()
plugin=stage/'decky-plugin'; plugin.mkdir()
for name in ['main.py','plugin.json','package.json','README.md','LICENSE']:
    shutil.copyfile(root/name,plugin/name)
shutil.copytree(root/'dist',plugin/'dist')
for folder,files in {'backend':['daemon.py','fan.py','power.py','autostart.py'],'scripts':['install.py','uninstall.py','control.py'], 'systemd':['claw-cpu-control.service']}.items():
    (stage/folder).mkdir()
    for f in files: shutil.copyfile(root/folder/f,stage/folder/f)
shutil.copyfile(root/'README.md',stage/'README.md')
manifest={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file()}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
zip_path=release/f'decky-claw-ex-control-{version}-decky.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
    for p in plugin.rglob('*'):
        if p.is_file(): z.write(p,Path('Claw EX Control')/p.relative_to(plugin))
tar_path=release/f'decky-claw-ex-control-{version}-install.tar.gz'
with tarfile.open(tar_path,'w:gz') as t: t.add(stage,arcname='decky-claw-ex-control')
(release/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in [zip_path,tar_path]))
print(zip_path);print(tar_path)
