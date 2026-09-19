"""Execute the already prepared, pinned CPU-only dependency setup for M7 tests.
No dependency resolution, source builds, model download, or existing-venv writes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib, json, os, re, shutil, subprocess, sys, time, tomllib, urllib.request, urllib.parse, zipfile
from pathlib import Path
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename, canonicalize_name

ROOT=Path(__file__).resolve().parents[4]
EVIDENCE=Path(__file__).resolve().parent
STATE=ROOT/'.feature-rl/research/M7/cpu-dependencies'
WHEELS=STATE/'wheels'
ENV=ROOT/'.venv-training-cpu'
PREP=ROOT/'docs/evidence/M0/cpu-training-dependency-preparation.md'
LOCK=ROOT/'uv.lock'
started=datetime.now(timezone.utc).isoformat()
if ENV.exists(): raise RuntimeError('CPU environment already exists; inspect it instead of replacing it')
WHEELS.mkdir(parents=True,exist_ok=True,mode=0o700)
prepared=json.loads(re.findall(r'```json\n(.*?)\n```',PREP.read_text(),re.S)[0])
selected={canonicalize_name(w['name']):dict(w) for w in prepared['wheels']}
core={'pydantic','pydantic-core','annotated-types','typing-inspection','pytest','packaging','pluggy','iniconfig','pygments','typing-extensions'}
tag_rank={tag:i for i,tag in enumerate(sys_tags())}
for package in tomllib.loads(LOCK.read_text())['package']:
    if package['name'] not in core: continue
    choices=[]
    for wheel in package['wheels']:
        filename=urllib.parse.unquote(urllib.parse.urlparse(wheel['url']).path.rsplit('/',1)[1])
        matches=[tag_rank[t] for t in parse_wheel_filename(filename)[3] if t in tag_rank]
        if matches: choices.append((min(matches),filename,wheel))
    if not choices: raise RuntimeError('No locked host wheel for '+package['name'])
    _,filename,wheel=min(choices)
    selected[package['name']]={'name':package['name'],'version':package['version'],'filename':filename,'url':wheel['url'],'size':wheel['size'],'sha256':wheel['hash'].removeprefix('sha256:')}
assert core <= selected.keys()
assert sum(w['size'] for w in selected.values()) <= 110*1024*1024

def acquire(w):
    target=WHEELS/w['filename']
    cache=ROOT/'.feature-rl/research/M2/downloads'/w['filename']
    source='retained wheelhouse'
    fetched=0
    if not target.exists():
        if cache.is_file():
            shutil.copyfile(cache,target)
            source='existing exact authoring wheel cache'
        else:
            source='download'
            partial=target.with_suffix('.part')
            before=time.monotonic()
            with urllib.request.urlopen(w['url'],timeout=30) as response, partial.open('xb') as out:
                if response.status != 200 or response.geturl()!=w['url']: raise RuntimeError('Unexpected wheel response')
                while True:
                    if time.monotonic()-before>120: raise TimeoutError('Wheel acquisition deadline')
                    chunk=response.read(min(1024*1024,w['size']+1-fetched))
                    if not chunk: break
                    fetched+=len(chunk)
                    if fetched>w['size']: raise RuntimeError('Wheel size exceeds exact receipt')
                    out.write(chunk)
            partial.replace(target)
    data=target.read_bytes()
    if len(data)!=w['size'] or hashlib.sha256(data).hexdigest()!=w['sha256']: raise RuntimeError('Wheel identity mismatch: '+w['filename'])
    with zipfile.ZipFile(target) as archive:
        names=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(names)!=1: raise RuntimeError('Ambiguous wheel metadata')
        metadata=BytesParser().parsebytes(archive.read(names[0]))
        if canonicalize_name(metadata['Name'])!=canonicalize_name(w['name']) or metadata['Version']!=w['version']: raise RuntimeError('Wheel metadata mismatch')
    result={**w,'source':source,'fetched_bytes':fetched,'verified_at':datetime.now(timezone.utc).isoformat()}
    print(w['name']+' '+w['version']+' verified ('+source+')',flush=True)
    return result

with ThreadPoolExecutor(max_workers=3) as pool:
    acquisitions=list(pool.map(acquire,selected.values()))
requirements=EVIDENCE/'requirements.txt'
requirements.write_text(''.join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in sorted(selected.values(),key=lambda x:x['name'])))
receipt={'scope':'CPU dependency setup only; no GPU or feature-learning claim','started_at':started,'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PREP,LOCK,Path(__file__).resolve())},'wheels':acquisitions,'fetched_bytes':sum(w['fetched_bytes'] for w in acquisitions),'commands':[]}

def run(command,name):
    begin=time.monotonic()
    result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
    (EVIDENCE/(name+'.log')).write_text(result.stdout)
    receipt['commands'].append({'argv':command,'exit_status':result.returncode,'wall_seconds':time.monotonic()-begin,'log':name+'.log'})
    (EVIDENCE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(name+': exit '+str(result.returncode),flush=True)
    result.check_returncode()

run([sys._base_executable,'-m','venv','--without-pip',str(ENV)],'create')
run(['uv','pip','install','--python',str(ENV/'bin/python'),'--offline','--no-index','--no-deps','--require-hashes','--only-binary',':all:','--find-links',str(WHEELS),'-r',str(requirements)],'install')
run(['uv','pip','check','--python',str(ENV/'bin/python')],'consistency')
run([str(ENV/'bin/python'),'-c',"import json,torch; x=torch.tensor([1.,2.],device='cpu'); print(json.dumps({'torch':torch.__version__,'device':str(x.device),'sum':x.sum().item(),'cuda_available':torch.cuda.is_available()}))"],'cpu-import')
receipt['finished_at']=datetime.now(timezone.utc).isoformat()
(EVIDENCE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Ready: '+str(ENV/'bin/python'),flush=True)
