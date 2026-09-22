"""M4 joins and controller outcomes; local cases are diagnostics, not qualification."""
from m4_fixtures import runtime_policy
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from m4_fixtures import diagnostic, replace_artifact


@pytest.fixture
def store(tmp_path):return ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)


def test_complete_manifest_joins_actual_m0_cases_and_preserves_seed_replay(store):
    from feature_rl.verifiers import load_verifier, materialize_manifest
    task_ref=diagnostic(store)
    checked=load_verifier(store,task_ref)
    first=materialize_manifest(checked,11)
    assert [case.case_id for case in first.cases]==['c0','c1']
    assert [case.input_plan for case in first.cases]==[case.inputs for case in checked.verifier.cases]
    assert first==materialize_manifest(checked,11)
    assert {r for case in first.cases for r in case.requirement_ids}=={'echo'}


@pytest.mark.parametrize('change',['task_contract','adapter','submission_policy','case_requirement','mandatory'])
def test_join_defects_reject_before_execution(store,change):
    from feature_rl.verifiers import load_verifier
    task_ref=diagnostic(store);task=store.get_artifact(task_ref);verifier=store.get_artifact(task.private_oracle)
    data=verifier.model_dump(mode='json')
    if change=='task_contract':
        other=replace_artifact(store,task.contract,visible_request='different diagnostic')
        task_ref=replace_artifact(store,task_ref,contract=other)
    elif change=='adapter':task_ref=replace_artifact(store,task_ref,adapter_version='other')
    else:
        if change=='submission_policy':data['permissions']['submission_policy']['forbidden_paths']=['src/x.py']
        if change=='case_requirement':data['cases'][0]['requirement_ids']=['unknown']
        if change=='mandatory':data['cases'][0]['mandatory']=False
        new=store.put_artifact(c.VerifierBundle.model_validate_json(json.dumps(data)))
        task_ref=replace_artifact(store,task_ref,private_oracle=new)
    with pytest.raises(ValueError):load_verifier(store,task_ref)


def test_receipt_cannot_claim_reward_for_incomplete_or_invalid_execution(store):
    from feature_rl.grading import GradeReceipt
    task=diagnostic(store)
    data=dict(version='m4-grade-v1',task=task.model_dump(mode='json'),submission=task.model_dump(mode='json'),
        verifier=store.get_artifact(task).private_oracle.model_dump(mode='json'),case_seed=11,manifest=None,source=None,
        disposition='success',reward=1,reason='forged',expected_case_ids=['c0'],cases=[],build_evidence=None,
        runtime_evidence=[],cleanup_verified=True,implementation_revision='a'*40,recorded_at='2026-09-19T00:00:00Z')
    with pytest.raises(ValueError):GradeReceipt.model_validate_json(json.dumps(data))


def test_grading_malformed_submission_is_zero_before_runtime(store):
    from feature_rl.grading import GradingService, read_grade
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    task=diagnostic(store)
    # Diagnostic at the pre-execution boundary only; real Docker tests exercise
    # build/probe behavior. This object has no callable execution methods mocked.
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy
    bad=store.put_bytes(b'{"version":"forged","reward":1}','m4-submission',c.Visibility.PRIVATE)
    service=GradingService(store=store,runtime=runtime,revision='a'*40)
    result=service.grade(task,bad,11)
    receipt=read_grade(store,result.artifacts[-1])
    assert result.operation=='grade' and receipt.reward==0
    assert receipt.submission==bad and receipt.case_seed==11
    assert [case.status for case in receipt.cases]==['not_run','not_run']


def test_missing_submission_is_unmeasured_and_replay_retains_seed(store):
    from feature_rl.grading import GradingService, read_grade
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    task=diagnostic(store)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy
    missing=c.ArtifactRef(sha256='f'*64,kind='m4-submission',schema_version=1,visibility=c.Visibility.PRIVATE,encoding='bytes')
    result=GradingService(store=store,runtime=runtime,revision='a'*40).grade(task,missing,12)
    receipt=read_grade(store,result.artifacts[-1])
    assert receipt.reward is None and receipt.submission==missing and receipt.case_seed==12
    assert result.disposition==c.Disposition.INFRASTRUCTURE


def test_grade_publication_recovery_reuses_exact_receipt_without_execution(store,monkeypatch):
    from feature_rl.grading import GradingService, GradePublicationFailed, read_grade
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    task=diagnostic(store)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy
    bad=store.put_bytes(b'{}','m4-submission',c.Visibility.PRIVATE)
    service=GradingService(store=store,runtime=runtime,revision='a'*40)
    real=store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m4-grade-receipt':raise OSError('diagnostic grade publication outage')
        return real(data,kind,visibility)
    with monkeypatch.context() as change:
        change.setattr(store,'put_bytes',outage)
        with pytest.raises(GradePublicationFailed) as caught:service.grade(task,bad,17)
    result=service.retry_publication(caught.value)
    receipt=read_grade(store,result.artifacts[0])
    assert receipt==caught.value.receipt and receipt.submission==bad and receipt.case_seed==17
    assert result.costs==caught.value.costs


def test_unsupported_contract_source_policy_is_not_reported_as_an_outage(store):
    from feature_rl.grading import GradingService, read_grade
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    task_ref=diagnostic(store);task=store.get_artifact(task_ref)
    contract=store.get_artifact(task.contract).model_dump(mode='json')
    contract['allowed_changes']['additional_artifact_types']=['unknown']
    cref=store.put_artifact(c.RequirementContract.model_validate_json(json.dumps(contract)))
    verifier=store.get_artifact(task.private_oracle).model_dump(mode='json')
    verifier['contract']=cref.model_dump(mode='json')
    verifier['permissions']['submission_policy']=contract['allowed_changes']
    vref=store.put_artifact(c.VerifierBundle.model_validate_json(json.dumps(verifier)))
    task_ref=replace_artifact(store,task_ref,contract=cref,private_oracle=vref)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy
    raw=store.put_bytes(b'{}','m4-submission',c.Visibility.PRIVATE)
    result=GradingService(store=store,runtime=runtime,revision='a'*40).grade(task_ref,raw,11)
    assert result.disposition==c.Disposition.UNSUPPORTED
    assert read_grade(store,result.artifacts[0]).reward is None


def task_with_limits(store,task_ref,updates):
    task=store.get_artifact(task_ref)
    contract=store.get_artifact(task.contract).model_dump(mode='json')
    contract['episode_limits'].update(updates)
    cref=store.put_artifact(c.RequirementContract.model_validate_json(json.dumps(contract)))
    verifier=store.get_artifact(task.private_oracle)
    vref=replace_artifact(store,task.private_oracle,contract=cref)
    return replace_artifact(store,task_ref,contract=cref,private_oracle=vref)


@pytest.mark.parametrize('field,limit',[
    ('wall_seconds',60.0),('cpu_seconds',1.0),('memory_bytes',64*1024*1024),
    ('pids',8),('disk_bytes',8*1024*1024),('output_bytes',1024*1024),
])
def test_recipe_cannot_exceed_any_contract_worker_limit(store,field,limit):
    from feature_rl.grading import GradingService, read_grade
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    task_ref=task_with_limits(store,diagnostic(store),{field:limit})
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy
    submission=store.put_bytes(b'{}','m4-submission',c.Visibility.PRIVATE)
    result=GradingService(store=store,runtime=runtime,revision='a'*40).grade(task_ref,submission,11)
    receipt=read_grade(store,result.artifacts[0])
    assert result.disposition==c.Disposition.UNSUPPORTED and receipt.reward is None
    assert receipt.build_evidence is None and receipt.source is None


def test_recipe_within_contract_limits_does_not_consume_solver_budgets(store):
    from feature_rl.verifiers import load_verifier
    original=diagnostic(store)
    equal=load_verifier(store,original)
    larger=task_with_limits(store,original,{'wall_seconds':240.0,'cpu_seconds':120.0,
        'memory_bytes':1024*1024*1024,'pids':128,'disk_bytes':256*1024*1024,
        'output_bytes':4*1024*1024,'tool_calls':1,'input_tokens':1,'output_tokens':1})
    accepted=load_verifier(store,larger)
    assert accepted.recipe.limits==equal.recipe.limits
    assert accepted.contract.episode_limits.tool_calls==1


@pytest.mark.parametrize('bad_part',['baseline_format','baseline_payload_size','baseline_expanded_size','candidate_format'])
def test_trusted_baseline_failures_are_null_but_candidate_delta_failure_is_zero(store,bad_part):
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy, SourceArchive, SourceFile
    from feature_rl.grading import GradingService, read_grade
    policy=runtime_policy()
    if bad_part=='baseline_payload_size':
        policy=runtime_policy(max_archive_bytes=10240)
        baseline_bytes=b'x'*10241
    elif bad_part=='baseline_expanded_size':
        policy=runtime_policy(max_source_bytes=16)
        baseline_bytes=SourceArchive({'src/click/__init__.py':SourceFile(b'x'*17,False)}).to_tar()
    elif bad_part=='baseline_format':baseline_bytes=b'not an archive'
    else:baseline_bytes=SourceArchive({'src/click/__init__.py':SourceFile(b'# valid source',False)}).to_tar()
    baseline=store.put_bytes(baseline_bytes,'source-archive',c.Visibility.AUTHORING)
    task_ref=diagnostic(store,baseline=baseline)
    delta_bytes=b'not an archive' if bad_part=='candidate_format' else SourceArchive({}).to_tar()
    delta=store.put_bytes(delta_bytes,'m4-source-delta',c.Visibility.PRIVATE)
    submission=store.put_bytes(canonical_json({'version':'m4-submission-v1','baseline':baseline.model_dump(mode='json'),
        'changes':delta.model_dump(mode='json'),'deletions':[]}),'m4-submission',c.Visibility.PRIVATE)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=policy;runtime.base_policy=policy
    result=GradingService(store=store,runtime=runtime,revision='a'*40).grade(task_ref,submission,11)
    receipt=read_grade(store,result.artifacts[0])
    if bad_part=='candidate_format':
        assert result.disposition==c.Disposition.REJECTED and receipt.reward==0
    elif bad_part in {'baseline_payload_size','baseline_expanded_size'}:
        # Configured archive caps are stricter than this fixture's frozen recipe.
        assert result.disposition==c.Disposition.UNSUPPORTED and receipt.reward is None
    else:
        assert result.disposition==c.Disposition.INFRASTRUCTURE and receipt.reward is None
    assert receipt.submission==submission and receipt.case_seed==11
    assert receipt.build_evidence is None and receipt.source is None
    assert all(case.status=='not_run' for case in receipt.cases)
