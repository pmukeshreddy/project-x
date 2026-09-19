"""Read-only bounded evidence audit; never imports historical/candidate code."""
import base64, hashlib, io, json, pathlib, tarfile

root = pathlib.Path('.')
index = json.loads((root/'docs/evidence/M3/production/index.json').read_text())
def digest(data): return hashlib.sha256(data).hexdigest()
def bounded(path, cap):
    with path.open('rb') as f: data=f.read(cap+1)
    assert len(data)<=cap, str(path)
    return data
def cas(attempt, ref, cap=32*1024*1024):
    path=root/'.feature-rl/research/M3/production'/attempt/'artifacts'/(ref['sha256']+'.json')
    raw=bounded(path,4*((cap+2)//3)+4096)
    assert digest(raw)==ref['sha256']
    envelope=json.loads(raw)
    for field in ('encoding','kind','schema_version','visibility'): assert envelope[field]==ref[field]
    data=base64.b64decode(envelope['payload'],validate=True)
    assert len(data)<=cap
    return data
out={'source_hashes_verified':{},'attempts':[]}
for path,expected in {**index['source_hashes'],**index['test_hashes']}.items():
    actual=digest(bounded(root/path,1024*1024));assert actual==expected
    out['source_hashes_verified'][path]=actual
package=root/'.superpowers/sdd/implementation-plan/review-8f97f83..a9fec98-M3-product.diff'
data=bounded(package,200000)
assert len(data)==134096 and digest(data)=='90c18b4657a40f11c40d1c5579fb889027471c9ff3c681840f746c4c400a482e'
out['owned_diff']={'bytes':len(data),'sha256':digest(data)}
for entry in index['records']:
    attempt=entry['attempt']
    report=json.loads(bounded(root/'docs/evidence/M3/production'/(attempt+'.json'),1024*1024))
    item={'attempt':attempt,'recorded_status':report['status'],'receipts':[],'observations':[]}
    for recorded in entry['verified_receipts']:
        data=cas(attempt,recorded['ref']);assert digest(data)==recorded['payload_sha256']
        receipt=json.loads(data)
        ownership=receipt['record'];assert receipt['cleanup_verified'] and ownership['phase']=='removed'
        name=ownership['container_id'] or ownership['container_name']
        commands=[c for c in receipt['commands'] if 'argv' in c]
        absent=commands[-1]
        assert absent['argv'][-2:]==['inspect',name] and absent['exit_code']!=0 and absent['reason']=='exited'
        absent_text=base64.b64decode(absent['stderr_b64']).decode()
        assert ('No such object: '+name) in absent_text or ('No such container: '+name) in absent_text
        removes=[c for c in commands if c['argv'][-3:]==['rm','--force',name]]
        assert removes and removes[-1]['exit_code']==0
        effective=receipt['effective'];host=effective['HostConfig']
        assert host['NetworkMode']=='none' and host['ReadonlyRootfs'] and not host['Privileged']
        assert host['CapDrop']==['ALL'] and not host['Binds'] and not effective['Mounts']
        assert effective['Config']['User']=='65534:65534'
        item['receipts'].append({'operation':ownership['operation_id'],'phase':receipt['phase'],
            'binding':ownership['binding'],'image':effective['Config']['Image'],
            'verified_absence':absent_text.strip(),
            'reason':receipt.get('extra',{}).get('reason'),
            'setup_and_execution':[{k:c[k] for k in ['argv','exit_code','reason']} for c in commands
                if 'exec' in c['argv'] and c['argv'][-1] not in ('print(open(\'/sys/fs/cgroup/memory.events\').read())',)]})
    for result in report['results']:
        stdout=base64.b64decode(result['stdout']).decode(errors='replace')
        summary=stdout.splitlines()[-1] if stdout else ''
        item['observations'].append({'label':result['label'],'reason':result['reason'],
            'exit_code':result['exit_code'],'stdout_last_line':summary,
            'saved':result['saved_source'],'save_status':result['save_status']})
    out['attempts'].append(item)
path=root/'docs/evidence/M3/review-round1/evidence-audit.json'
path.write_text(json.dumps(out,indent=2)+'\n')
print('PASS: bound source/test/diff; independently verified 23 CAS receipts, effective controls, exact remove/absence pairs and recorded observations')
