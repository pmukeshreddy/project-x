"""Contract acceptance tests; synthetic values are unit diagnostics only."""
import importlib
import json
import pytest
from pydantic import ValidationError


def api():
    module = importlib.import_module('feature_rl.contracts')
    assert hasattr(module, 'ArtifactRef'), 'M0 strict contracts are not implemented'
    return module


def ref(kind='source', visibility='private'):
    return dict(sha256='a'*64, kind=kind, schema_version=1, visibility=visibility, encoding='bytes')


def cost():
    return dict(category='construction', wall_seconds=None, cpu_seconds=None, gpu_seconds=None,
                input_tokens=None, output_tokens=None, human_minutes=None, usd=None,
                measurement='unknown', note='Not measured in this unit diagnostic')


def evidence():
    return dict(producer='pytest', command=['pytest'], recorded_at='2026-09-19T00:00:00Z',
                exit_status=0, artifacts=[ref()], revision='a'*40, scope='unit_diagnostic')


def test_unknown_and_coerced_reference_values_are_rejected():
    m=api()
    for update in ({'schema_version':True}, {'schema_version':'1'}, {'sha256':'../bad'},
                   {'unexpected':1}, {'kind':'../source'}, {'visibility':'typo'}):
        with pytest.raises(ValidationError):
            m.ArtifactRef.model_validate_json(json.dumps(ref() | update))


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-float('inf'),True,'1.0',-1])
def test_cost_does_not_coerce_or_accept_nonfinite_values(bad):
    m=api()
    with pytest.raises(ValidationError):
        m.CostRecord.model_validate_json(json.dumps(cost() | {'usd':bad}))


def test_unknown_cost_is_explicit_and_success_needs_evidence():
    m=api()
    assert m.CostRecord.model_validate_json(json.dumps(cost())).usd is None
    args=dict(operation='construct', disposition='success', artifacts=[ref()], evidence=[], costs=[cost()], reason='Built')
    with pytest.raises(ValidationError):
        m.OperationResult.model_validate_json(json.dumps(args))
    args['evidence']=[evidence()]
    assert m.OperationResult.model_validate_json(json.dumps(args)).disposition.value == 'success'
    del args['costs']
    with pytest.raises(ValidationError):
        m.OperationResult.model_validate_json(json.dumps(args))


def test_models_cannot_sign_human_review():
    m=api()
    data=dict(actor_type='model', human_identity='pretend human', subject_sha256='a'*64,
              decision='approved', evidence=[evidence()], attestation=ref('attestation'))
    with pytest.raises(ValidationError):
        m.HumanReview.model_validate_json(json.dumps(data))


def test_requirement_requires_grounding():
    m=api()
    with pytest.raises(ValidationError):
        m.Requirement.model_validate_json(json.dumps(dict(requirement_id='R1', statement='Add behavior', mandatory=True, evidence=[], observable='cli output')))


ARTIFACT_KINDS=('CandidateRecord','SourcePair','RequirementContract','EnvironmentRecipe','VerifierBundle','TaskBundle','QualificationReport','RolloutRecord','TrainingCheckpoint','EvaluationReport')


@pytest.mark.parametrize('kind',ARTIFACT_KINDS)
def test_all_artifacts_reject_empty_payloads_and_publish_schemas(kind):
    m=api()
    model=getattr(m,kind)
    with pytest.raises(ValidationError): model.model_validate_json('{}')
    assert len(model.model_json_schema()['required']) >= 5


@pytest.mark.parametrize('bad_revision',['a'*41,'a'*63,' '*40])
def test_evidence_revision_is_an_exact_git_digest(bad_revision):
    m=api(); data=evidence()|{'revision':bad_revision}
    with pytest.raises(ValidationError): m.EvidenceRecord.model_validate_json(json.dumps(data))


def test_evidence_requires_utc_and_nonempty_producer():
    m=api()
    for update in ({'recorded_at':'2026-09-19T00:00:00'}, {'recorded_at':'2026-09-19T00:00:00+01:00'},{'producer':'   '}):
        with pytest.raises(ValidationError):m.EvidenceRecord.model_validate_json(json.dumps(evidence()|update))


def test_grade_request_rejects_boolean_seed_and_wrong_task_kind():
    m=api()
    assert hasattr(m,'GradeRequest'), 'shared operation requests missing'
    task=ref('TaskBundle')|{'encoding':'json'}
    for fields in ({'case_seed':True},{'task_version':ref()}):
        with pytest.raises(ValidationError):
            m.GradeRequest.model_validate_json(json.dumps(dict(task_version=task,submission=ref(),case_seed=1)|fields))


def test_success_cannot_be_signed_by_only_failed_command_evidence():
    m=api()
    data=dict(operation='grade',disposition='success',artifacts=[ref()],evidence=[evidence()|{'exit_status':1}],costs=[cost()],reason='claimed success')
    with pytest.raises(ValidationError):m.OperationResult.model_validate_json(json.dumps(data))
