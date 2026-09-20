"""Inert supervised renderer diagnostics; no admitted solution or model claim."""
import json
import pytest
from feature_rl.environments import SourceArchive,SourceRejected
from feature_rl.environments.archive import SourceFile
from feature_rl.agents.protocol import parse_action,execution_request
from feature_rl.training.supervision import render_source_action
from feature_rl.contracts import AllowedChanges

def rules():
    return AllowedChanges(source_roots=('src/click',),forbidden_paths=('src/click/private.py',),
        dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())

def archive(**files):
    return SourceArchive({k:SourceFile(v.encode(),False) for k,v in files.items()})

def test_solution_renderer_preserves_exact_write_delete_and_uses_actual_action_schema():
    before=archive(**{'src/click/old.py':'old\n','src/click/keep.py':'keep'})
    after=archive(**{'src/click/new.py':'value="unicode λ"\n','src/click/keep.py':'keep'})
    target=render_source_action(before,after,rules())
    action=parse_action(target);request=execution_request(action,10.)
    payload=json.loads(request.stdin)
    assert payload=={'delete':['src/click/old.py'],'write':{'src/click/new.py':'value="unicode λ"\n'}}
    assert request.command.argv[:3]==('python','-I','-c')
    assert 'private grading' not in target
    assert render_source_action(before,before,rules())=='{"action":"submit"}'

@pytest.mark.parametrize('name',['tests/test.py','src/click/private.py','src/click/data.json'])
def test_renderer_rejects_forbidden_or_unsupported_targets(name):
    with pytest.raises(SourceRejected): render_source_action(archive(),archive(**{name:'x'}),rules())

def test_renderer_rejects_oversized_solution_without_truncation():
    with pytest.raises(ValueError):
        render_source_action(archive(),archive(**{'src/click/a.py':'x'*300000}),rules())


def test_importer_keeps_admission_and_actual_submission_grade_join(tmp_path,monkeypatch):
    # Isolated CPU service boundary fixture, not a real release/grade/model result.
    from test_agent_runner import fixture
    from feature_rl.training.supervision import DemonstrationImporter
    from feature_rl.grading.models import GradeReceipt,CaseResult,AssertionResult
    from feature_rl import contracts as c
    from datetime import datetime,timezone
    from types import SimpleNamespace
    f=fixture(tmp_path,monkeypatch)
    from m4_fixtures import replace_artifact
    f.task=replace_artifact(f.store,f.task,partition=c.Partition.TRAIN)
    task=f.store.get_artifact(f.task)
    contract=f.store.get_artifact(task.contract)
    source=f.runner.grader.submissions.source(task.baseline)
    files=dict(source.files);files['src/click/sft_diagnostic.py']=SourceFile(b'answer = 1\n',False)
    after=SourceArchive(files)
    ref=f.store.put_bytes(after.to_tar(),'source-archive',c.Visibility.PRIVATE)
    submission=f.runner.grader.submissions.from_saved(task.baseline,ref,contract.allowed_changes)
    receipt=GradeReceipt(version='m4-grade-v1',task=f.task,submission=submission,verifier=task.private_oracle,
        case_seed=19,manifest=ref,source=ref,disposition=c.Disposition.SUCCESS,reward=1,
        reason='Isolated diagnostic grade shape; no worker execution',expected_case_ids=('diagnostic',),
        cases=(CaseResult(case_id='diagnostic',mandatory=True,status='completed',passed=True,
            assertions=(AssertionResult(assertion_id='a',requirement_ids=('r',),passed=True),),evidence=ref,reason='diagnostic'),),
        build_evidence=ref,runtime_evidence=(ref,),cleanup_verified=True,implementation_revision=f.runner.grader.revision,
        recorded_at=datetime.now(timezone.utc))
    result=f.runner.grader._publish(receipt,(task.costs[0],),'unit_diagnostic')
    grade=result.artifacts[0];f.runner._declare_submission(submission);f.runner._declare_grade(grade)
    f.backend.tokenizer=SimpleNamespace(encode=lambda text,add_special_tokens:tuple(text.encode()))
    importer=DemonstrationImporter(f.runner)
    example=importer.source(task=f.task,submission=submission,grade=grade,policy=f.policy)
    assert len(example.turns)==1 and example.turns[0].behavior is None and example.turns[0].advantage is None
    target=bytes(example.turns[0].targets).decode();assert 'sft_diagnostic.py' in target
    assert not f.backend.calls and not f.actions and not f.grades
    private=f.store.put_bytes(b'PRIVATE TEST marker','system-prompt',c.Visibility.PRIVATE)
    with pytest.raises(ValueError,match='public system prompt'):
        importer.source(task=f.task,submission=submission,grade=grade,
            policy=f.policy.model_copy(update={'system_prompt':private}))
    bad=f.store.put_bytes(b'different submission','m4-submission',c.Visibility.PRIVATE)
    with pytest.raises(ValueError,match='exact task/submission'):
        importer.source(task=f.task,submission=bad,grade=grade,policy=f.policy)
    def denied(ref): raise ValueError('actual admission denied')
    importer.gate._admit=denied
    with pytest.raises(ValueError,match='admission denied'):
        importer.source(task=f.task,submission=submission,grade=grade,policy=f.policy)
