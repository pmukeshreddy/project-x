"""Bounded read-only audit of existing round2 fix evidence; no runtime execution."""
import base64, hashlib, json, pathlib

root=pathlib.Path('.')
def read(path,cap=2*1024*1024):
    with (root/path).open('rb') as f:data=f.read(cap+1)
    assert len(data)<=cap,path
    return data
def digest(data):return hashlib.sha256(data).hexdigest()
verification=json.loads(read('docs/evidence/M3/fix-round2/verification.json'))
hashes={**verification['source_hashes'],**verification['test_hashes'],**verification['evidence_files']}
for path,expected in hashes.items():assert digest(read(path))==expected,path
package=read('.superpowers/sdd/implementation-plan/review-ad154f8..2190af0-M3-fix.diff')
assert len(package)==16418 and digest(package)=='c73410f8139800153406ca7a2e065ec8d50bb4372c9eb1a84339037491853a97'
report=json.loads(read('docs/evidence/M3/fix-round2/terminal-cleanup-result.json'))
assert report['status']=='passed' and report['source_hashes']==verification['source_hashes']
assert report['driver_sha256']==hashes['docs/evidence/M3/fix-round2/terminal_cleanup.py']
summary=[]
for case in report['cases']:
    pending=case['pending_before_terminal'];cid=pending['container_id'];op=pending['operation_id']
    assert pending['phase']=='cleanup_pending' and case['pending_after_terminal']==pending
    for name in ('live_before_terminal','live_after_terminal'):
        assert case[name]['status']==200 and case[name]['id']==cid and case[name]['state']['Running'] is True
    assert case['terminal_rejection']['type']=='CleanupUnverified'
    before=case['workspace_before_terminal'];after=case['workspace_after_terminal']
    assert before==after and before['generation']==1 and before['closed'] is False
    assert before['saved']==case['execution']['saved_source'] and before['saved']['version']==1
    ref=case['execution']['evidence']
    envelope=read('.feature-rl/research/M3/fix-round2/terminal-cleanup-1/artifacts/'+ref['sha256']+'.json',48*1024*1024)
    assert digest(envelope)==ref['sha256'];value=json.loads(envelope)
    for key in ('encoding','kind','schema_version','visibility'):assert value[key]==ref[key]
    data=base64.b64decode(value['payload'],validate=True);assert len(data)<=32*1024*1024
    execution=json.loads(data);assert execution==case['execution_evidence']
    assert execution['record']['operation_id']==op and execution['record']['container_id']==cid
    assert execution['cleanup_verified'] is False and execution['extra']['failure_category']=='infrastructure'
    recovery=[r for r in case['recovery'] if r['operation_id']==op]
    assert len(recovery)==1 and recovery[0]['cleanup_verified'] is True
    commands=recovery[0]['receipts'];remove,absent=commands[-2:]
    assert remove['argv'][-3:]==['rm','--force',cid] and remove['exit_code']==0 and remove['reason']=='exited'
    assert absent['argv'][-2:]==['inspect',cid] and absent['exit_code']!=0 and absent['reason']=='exited'
    absent_message=base64.b64decode(absent['stderr_b64']).decode()
    assert ('No such object: '+cid) in absent_message
    assert case['absent_after_recovery']['status']==404
    final=case['workspace_after_retry']
    assert final['generation']==2 and final['closed']==(case['transition']=='close')
    expected=before['saved'] if case['transition']=='close' else case['initial']
    assert case['retry_saved']==expected and final['saved']==expected
    assert case['remaining_recovery']==[] and case['passed'] is True
    summary.append({'transition':case['transition'],'operation_id':op,'container_id':cid,
        'failed_admission_preserved_workspace':True,'generation_at_rejection':after['generation'],
        'generation_after_retry':final['generation'],'source_version_after_retry':final['saved']['version'],
        'payload_sha256':digest(data),'exact_remove_and_absence_verified':True})
assert len(summary)==2 and report['remaining_recovery']==[]
assert len(report['operation_records'])==3 and all(r['phase']=='removed' for r in report['operation_records'])
red=json.loads(read('docs/evidence/M3/fix-round2/red-receipt.json'))
assert red['test_sha256']==verification['test_hashes']['tests/test_environments_docker.py']
assert red['source_hashes']['src/feature_rl/environments/runtime.py']=='cd0eb49c91738c4aaac11d4848ad5a5bb0c264e48a6aa034827a52c59c1f4d9d'
assert red['exit_status']==1
out={'status':'passed','owned_diff_sha256':digest(package),'verified_file_hashes':hashes,
    'cases':summary,'all_three_diagnostic_operations_removed':True,
    'new_runtime_execution':False,'note':'Existing real diagnostic/CAS evidence audited with bounded inert reads.'}
(root/'docs/evidence/M3/review-round3/audit-result.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'status':'passed','verified_file_count':len(hashes),'cases':summary}))
