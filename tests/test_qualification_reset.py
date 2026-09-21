"""One actual reset operation, exercised with a synthetic runtime only."""
from types import SimpleNamespace
import pytest
from feature_rl.environments import SourceArchive
from feature_rl.qualification import QualificationRejected, ResetReceipt, derive_reference
from feature_rl.qualification.evidence import put_record, reset_probe, RESET_PROBE_BYTES
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from m5_fixtures import task_fixture
from test_qualification_service import service


@pytest.mark.parametrize('defect',[None,'unchanged_mutation','other_mutation','wrong_reset','generation','cleanup'])
def test_reset_requires_saved_mutation_exact_restoration_and_cleanup(tmp_path,monkeypatch,defect):
    q=service(tmp_path);task=task_fixture(q.store);checked=load_verifier(q.store,task)
    projection=derive_reference(q.store,task,q.grader.submissions.policy)
    pref=put_record(q.store,projection,'m5-reference-projection')
    source=q.grader.submissions.source(projection.projected_source)
    runtime=q.grader.runtime;runtime.profile=runtime.policy.profile
    initial=runtime.saved(source,0)
    path,entry=reset_probe(source,checked.contract.allowed_changes,runtime.profile)
    dirty=runtime.saved(SourceArchive(dict(source.files)|{path:entry}),1)
    if defect=='other_mutation':
        from feature_rl.environments import SourceFile
        dirty=runtime.saved(SourceArchive(dict(source.files)|{path:SourceFile(b'other bytes',False)}),1)
    calls=[];state={'generation':0}
    handle=SimpleNamespace(workspace_id='a'*32)
    monkeypatch.setattr(runtime,'open_workspace',lambda *args,**kwargs:handle)
    monkeypatch.setattr(runtime,'workspace',lambda *args,**kwargs:(dict(state),None,None,initial,source))
    def mutate(handle,request):
        assert request.save_source and request.stdin==RESET_PROBE_BYTES
        calls.append('mutation');state['generation']+=1
        return SimpleNamespace(reason='completed',cleanup_verified=defect!='cleanup',
            saved_source=initial if defect=='unchanged_mutation' else dirty,
            evidence=checked.task.provenance.evidence[0].artifacts[0],
            operation_id='b'*32,cost=checked.task.costs[0])
    def reset(handle):
        calls.append('reset')
        if defect!='generation':state['generation']+=1
        return dirty if defect=='wrong_reset' else initial
    monkeypatch.setattr(runtime,'execute_development',mutate)
    monkeypatch.setattr(runtime,'reset',reset)
    monkeypatch.setattr(runtime,'close',lambda handle:calls.append('close'))
    if defect:
        with pytest.raises(QualificationRejected):q._reset(checked,pref,projection.submission)
    else:
        ref,costs=q._reset(checked,pref,projection.submission)
        record=read_local(q.store,ref,ResetReceipt,'m5-reset')
        assert record.initial_source==record.reset_source==projection.projected_source
        assert record.generation_after==record.generation_before+1
        assert calls==['mutation','reset','close'] and len(costs)==1
    assert calls[-1]=='close'
