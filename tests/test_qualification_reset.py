"""Reset receipt replay on explicit synthetic saved-source evidence; no worker."""
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import SourceArchive
from feature_rl.environments.models import Ownership
from feature_rl.qualification import QualificationRejected, ResetReceipt, ReferenceProjection, derive_reference
from feature_rl.qualification.evidence import put_record, reset_probe, RESET_PROBE_BYTES, validate_reset
from feature_rl.verifiers import load_verifier
from m5_fixtures import task_fixture
from test_qualification_service import service


def reset_fixture(tmp_path):
    q=service(tmp_path);task=task_fixture(q.store);checked=load_verifier(q.store,task)
    projection=derive_reference(q.store,task,q.grader.submissions.policy)
    pref=put_record(q.store,projection,'m5-reference-projection')
    prepared=q.grader.select_task(checked)
    source=q.grader.submissions.source(projection.projected_source)
    initial=q.grader.runtime.saved(source,0)
    path,entry=reset_probe(source,checked.contract.allowed_changes,q.grader.runtime.policy.profile)
    dirty=q.grader.runtime.saved(SourceArchive(dict(source.files)|{path:entry}),1)
    workspace='a'*32
    binding={'recipe':prepared.recipe.sha256,'policy':prepared.policy.sha256,'dependency_resolution':'baseline-tools',
        'revision':q.grader.runtime.revision,'role':'candidate','phase':'development','workspace_id':workspace,
        'generation':'0','source_input':projection.projected_source.sha256,'source':projection.projected_source.sha256,
        'source_raw':initial.raw_sha256,'tree':initial.tree_sha256,
        'allowed_changes':hashlib.sha256(canonical_json(checked.contract.allowed_changes.model_dump(mode='json'))).hexdigest()}
    owner=Ownership(operation_id='b'*32,owner_token='c'*32,daemon_id='synthetic',container_name='synthetic-reset',
        container_id='synthetic-container',phase='removed',binding=binding,saved_source=initial.model_dump(mode='json'),
        created_at='2026-09-20T00:00:00Z')
    command=['/usr/local/bin/python','-I','-c','import pathlib,sys\nwith pathlib.Path(sys.argv[1]).open("ab") as stream: stream.write(sys.stdin.buffer.read())','/workspace/source/'+path]
    value={'phase':'development','revision':q.grader.runtime.revision,'cleanup_verified':True,'record':owner.model_dump(mode='json'),
        'commands':[{'argv':['diagnostic-docker','exec',owner.container_id,*command],'reason':'exited','exit_code':0,
                     'stdin_bytes':len(RESET_PROBE_BYTES),'stdin_sha256':hashlib.sha256(RESET_PROBE_BYTES).hexdigest()}],
        'extra':{'saved_source':dirty.model_dump(mode='json'),'save_status':'saved','reason':'completed',
            'failure_category':'none','error':None,'dependency_resolution':None,
            'dependency_failure':{'type':'SourceRejected','source_tree_sha256':source.tree_sha256,'reason':'Synthetic dependency diagnostic'}}}
    mutation=put_record(q.store,value,'environment-execution')
    record=ResetReceipt(task=task,projection=pref,workspace_id=workspace,mutation=mutation,mutation_operation=owner.operation_id,
        initial_source=initial.artifact,reset_source=initial.artifact,generation_before=1,generation_after=2,
        cleanup_verified=True,recorded_at=datetime.now(timezone.utc))
    return q,checked,pref,record,value,dirty


def test_saved_canary_reset_receipt_requires_exact_dirty_and_restored_gold(tmp_path):
    q,checked,pref,record,value,dirty=reset_fixture(tmp_path)
    ref=put_record(q.store,record,'m5-reset');seen=set()
    assert validate_reset(q.store,checked,pref,ref,q.grader,seen=seen)==record
    with pytest.raises(QualificationRejected,match='replayed'):
        validate_reset(q.store,checked,pref,ref,q.grader,seen=seen)
    for bad in (record.model_copy(update={'reset_source':dirty.artifact}),
                record.model_copy(update={'generation_after':record.generation_before}),
                record.model_copy(update={'cleanup_verified':False})):
        with pytest.raises(QualificationRejected):
            validate_reset(q.store,checked,pref,put_record(q.store,bad,'m5-reset'),q.grader,seen=set())
    value['extra']['saved_source']=value['record']['saved_source']
    bad=record.model_copy(update={'mutation':put_record(q.store,value,'environment-execution')})
    with pytest.raises(QualificationRejected,match='diagnostic mutation'):
        validate_reset(q.store,checked,pref,put_record(q.store,bad,'m5-reset'),q.grader,seen=set())


def test_reset_checks_one_saved_mutation_then_restores_and_closes(tmp_path,monkeypatch):
    from feature_rl.environments.models import SavedSource
    from feature_rl.verifiers.loader import read_local
    q,checked,pref,record,value,dirty=reset_fixture(tmp_path)
    runtime=q.grader.runtime
    runtime.profile=runtime.policy.profile
    initial=SavedSource.model_validate_json(canonical_json(value['record']['saved_source']))
    source=q.grader.submissions.source(initial.artifact)
    calls=[];state={'generation':0}
    handle=SimpleNamespace(workspace_id=record.workspace_id)
    monkeypatch.setattr(runtime,'open_workspace',lambda *args,**kwargs:handle)
    monkeypatch.setattr(runtime,'workspace',lambda *args,**kwargs:(dict(state),None,None,initial,source))
    def mutate(handle,request):
        assert request.save_source and request.stdin==RESET_PROBE_BYTES
        calls.append('mutation');state['generation']+=1
        return SimpleNamespace(reason='completed',cleanup_verified=True,saved_source=dirty,evidence=record.mutation,
            operation_id=record.mutation_operation,cost=checked.task.costs[0])
    def reset(handle):
        calls.append('reset');state['generation']+=1
        return initial
    monkeypatch.setattr(runtime,'execute_development',mutate)
    monkeypatch.setattr(runtime,'reset',reset)
    monkeypatch.setattr(runtime,'close',lambda handle:calls.append('close'))
    projection=read_local(q.store,pref,ReferenceProjection,'m5-reference-projection')
    ref,costs=q._reset(checked,pref,projection.submission)
    assert calls==['mutation','reset','close'] and len(costs)==1
    assert validate_reset(q.store,checked,pref,ref,q.grader,seen=set()).reset_source==projection.projected_source
