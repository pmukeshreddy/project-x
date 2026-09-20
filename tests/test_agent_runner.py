"""Runner mechanics with explicitly substituted service boundaries, never admission evidence.

No accepted Q, HumanReview, model output or released task is manufactured. The
fixture's BUILT diagnostic task bypasses admission/package only within these tests;
production constructors and all CAS/Registry/rollout accounting code remain actual.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.agents import AgentRunner, PolicyBackend, Completion, HARNESS, InvalidGeneration
from feature_rl.agents.protocol import parse_action, execution_request
from feature_rl.environments import EnvironmentRuntime, SandboxPolicy, SavedSource, ExecutionResult
from feature_rl.grading import GradingService, read_grade
from feature_rl.pipeline import TaskLifecycle, TaskBuilder, AdmissionRejected
from feature_rl.registry import Registry, RegistryError
from m5_fixtures import task_fixture
from m4_fixtures import replace_artifact

class DiagnosticBackend(PolicyBackend):
    max_seq_len=8192;vocab_size=256;tokenizer_digest='c'*64;template_digest='d'*64
    def __init__(self, outputs): self.outputs=list(outputs);self.calls=[];self.contexts=[];self.fail=None;self.probs=True;self.stale=False
    def verify_policy(self,policy):
        if self.fail: raise self.fail
    def render(self,messages):
        self.contexts.append(list(messages))
        rendered=json.dumps(messages)
        return rendered,tuple(rendered.encode())
    def generate(self,context,*,policy,max_tokens,timeout,session_id):
        self.calls.append((context,policy,max_tokens,session_id))
        text=self.outputs.pop(0);ids=tuple(text.encode())
        return Completion(text,context,ids,(-1.,)*len(ids) if self.probs else None,
            'stale' if self.stale else policy.policy_version,'stop')


def fixture(tmp_path,monkeypatch,outputs=(' {"action":"submit"}',)):
    store=ArtifactStore(tmp_path.resolve()/'objects',c.ActorRole.CONTROLLER)
    registry=Registry(tmp_path.resolve()/'registry',store)
    task_ref=task_fixture(store);task=store.get_artifact(task_ref)
    # Supply only the exact sandbox policy join absent from the earlier M4 inert fixture.
    policy_ref=store.put_bytes(canonical_json(SandboxPolicy().model_dump(mode='json')),'sandbox-policy',c.Visibility.PRIVATE)
    recipe=store.get_artifact(task.environment)
    recipe=recipe.model_copy(update={'provenance':recipe.provenance.model_copy(update={'inputs':(*recipe.provenance.inputs,policy_ref)})})
    recipe_ref=store.put_artifact(recipe)
    task_ref=replace_artifact(store,task_ref,environment=recipe_ref);task=store.get_artifact(task_ref)
    contract=store.get_artifact(task.contract)
    limits=contract.episode_limits.model_copy(update={'input_tokens':100000,'output_tokens':10000,'tool_calls':4})
    contract=contract.model_copy(update={'episode_limits':limits})
    contract_ref=store.put_artifact(contract)
    task_ref=replace_artifact(store,task_ref,contract=contract_ref);task=store.get_artifact(task_ref)
    backend=DiagnosticBackend(outputs)
    lifecycle=object.__new__(TaskLifecycle);lifecycle.store=store;lifecycle.registry=registry;lifecycle.revision='b'*40
    lifecycle.configuration=store.put_bytes(b'diagnostic only lifecycle configuration','m6-lifecycle-policy',c.Visibility.PRIVATE)
    monkeypatch.setattr(lifecycle,'resolve_released',lambda ref:store.get_artifact(ref))
    builder=TaskBuilder(store=store,registry=registry,revision='b'*40)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=SandboxPolicy();runtime.revision='c'*40
    handles=[];actions=[];closes=[]
    saved=SavedSource(artifact=task.baseline,raw_sha256='e'*64,tree_sha256='f'*64,version=0,saved_at='2026-09-19T00:00:00Z')
    def open_workspace(*a,**kw):
        handles.append(SimpleNamespace(index=len(handles)));return handles[-1]
    monkeypatch.setattr(runtime,'open_workspace',open_workspace)
    monkeypatch.setattr(runtime,'close',lambda h:(closes.append(h) or saved))
    evidence=store.put_bytes(b'diagnostic execution outcome','m3-execution',c.Visibility.PRIVATE)
    execution_cost=c.CostRecord(category='execution',wall_seconds=.01,cpu_seconds=.001,gpu_seconds=None,
        input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='Diagnostic only')
    def execute(handle,request):
        actions.append((handle,request))
        return ExecutionResult(operation_id='a'*32,reason='command_failed',failure_category='candidate',exit_code=1,
            stdout=b'',stderr=b'diagnostic ordinary failure',container_exit_code=0,oom_killed=False,maximum_memory_bytes=100,
            cleanup_verified=True,saved_source=saved,save_status='saved',evidence=evidence,cost=execution_cost)
    monkeypatch.setattr(runtime,'execute_development',execute)
    grader=GradingService(store=store,runtime=runtime,revision='a'*40)
    # Actual M4 source rejection is used, but diagnostic task joins above are not a
    # valid verifier contract. An actual result from the original complete M4 task
    # is rebound ONLY by the isolated grade test double, never presented as a real grade.
    from feature_rl.grading.models import GradeReceipt
    grades=[]
    def grade(ref,submission,seed):
        from datetime import datetime,timezone
        grades.append((ref,submission,seed))
        receipt=GradeReceipt(version='m4-grade-v1',task=ref,submission=submission,verifier=None,case_seed=seed,
            manifest=None,source=None,disposition=c.Disposition.REJECTED,reward=0,reason='source submission rejected: diagnostic',
            expected_case_ids=(),cases=(),build_evidence=None,runtime_evidence=(),cleanup_verified=True,
            implementation_revision=grader.revision,recorded_at=datetime.now(timezone.utc))
        return grader._publish(receipt,(execution_cost,),'source_inspection')
    monkeypatch.setattr(grader,'grade',grade)
    system=store.put_bytes(b'Follow the fixed source action protocol.','system-prompt',c.Visibility.PUBLIC)
    policy=c.PolicyConfig(identity=c.ModelIdentity(provider='diagnostic',model='diagnostic',revision='1',weights=None,
        tokenizer_digest=backend.tokenizer_digest),policy_version='diagnostic-v1',temperature=1.,top_p=1.,seed=12,
        system_prompt=system,harness_version=HARNESS,require_token_probabilities=True)
    runner=AgentRunner(store=store,registry=registry,lifecycle=lifecycle,builder=builder,runtime=runtime,grader=grader,
        backend=backend,revision='d'*40,evidence_scope='unit_diagnostic')
    monkeypatch.setattr(runner,'_messages',lambda task:[{'role':'user','content':'DIAGNOSTIC instruction/public checks only'}])
    return SimpleNamespace(runner=runner,store=store,registry=registry,task=task_ref,policy=policy,limits=limits,
        backend=backend,grades=grades,actions=actions,handles=handles,closes=closes)


def record(f,result):
    refs=[x for x in result.artifacts if x.kind=='RolloutRecord'];assert len(refs)==1
    return f.store.get_artifact(refs[0])


def test_run_id_case_seed_and_replay_are_exact_selected_registry_job(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    result=f.runner.run(f.task,f.policy,f.limits,case_seed=71,invocation='trial-one')
    r=record(f,result)
    assert r.run_id==f.registry.job(r.run_id).job_id
    assert r.seeds.seeds==(71,) and f.grades[0][2]==71
    assert len(f.grades)==len(f.backend.calls)==1
    assert f.runner.validate_record(r).reward==0
    assert f.runner.run(f.task,f.policy,f.limits,case_seed=71,invocation='trial-one')==result
    assert len(f.grades)==len(f.backend.calls)==1
    assert r.training_eligible and r.steps[0].token_trace.context_token_ids==f.backend.calls[0][0]


def test_ordinary_failure_continues_with_fresh_worker_and_only_public_feedback(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('{"action":"command","argv":["false"]}','{"action":"submit"}'))
    result=f.runner.run(f.task,f.policy,f.limits)
    r=record(f,result)
    assert r.stopping_reason==c.StopReason.SUBMITTED and r.reward==0
    assert len(r.steps)==2 and len(f.actions)==1 and len(f.closes)==1
    assert f.grades[0][2]==f.policy.seed
    conversation=json.dumps(f.backend.contexts[-1])
    assert 'diagnostic ordinary failure' in conversation
    assert 'private_oracle' not in conversation and 'reference_solution' not in conversation
    assert 'source submission rejected' not in conversation  # hidden grade happens after final action


def test_malformed_action_is_measured_on_saved_source(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('not json',))
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert r.stopping_reason==c.StopReason.MALFORMED_ACTION and r.reward==0
    assert len(f.grades)==1 and not f.actions and r.training_eligible


@pytest.mark.parametrize('failure',['stale','missing_probs'])
def test_invalid_generation_has_authentic_null_controller_outcome(tmp_path,monkeypatch,failure):
    f=fixture(tmp_path,monkeypatch)
    if failure=='stale':f.backend.stale=True
    else:f.backend.probs=False
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert r.reward is None and r.disposition==c.Disposition.INVALID
    assert not f.grades and not r.training_eligible
    assert f.runner.validate_record(r).stopping_reason==c.StopReason.INVALID_TRAJECTORY
    altered=r.model_copy(update={'run_id':'f'*64})
    with pytest.raises((ValueError,RegistryError)):f.runner.validate_record(altered)


def test_no_logprobs_is_honest_nontraining_when_policy_allows_it(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);f.backend.probs=False
    policy=f.policy.model_copy(update={'require_token_probabilities':False})
    r=record(f,f.runner.run(f.task,policy,f.limits))
    assert r.reward==0 and not r.training_eligible and r.steps[0].token_trace is None


def test_real_admission_denial_precedes_any_backend_or_worker_call(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    # Restore the actual production resolver; BUILT is rejected before private Q is read.
    monkeypatch.setattr(f.runner.lifecycle,'resolve_released',TaskLifecycle.resolve_released.__get__(f.runner.lifecycle))
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert r.reward is None and r.disposition==c.Disposition.INVALID
    assert not f.backend.calls and not f.handles and not f.grades
    assert 'released' in f.runner.validate_record(r).reason


def test_tool_limit_grades_last_confirmed_source(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('{"action":"write","path":"src/click/file.py","content":"x=1"}',))
    limits=f.limits.model_copy(update={'tool_calls':1})
    r=record(f,f.runner.run(f.task,f.policy,limits))
    assert r.stopping_reason==c.StopReason.TOOL_LIMIT and r.reward==0 and len(f.actions)==1
    assert f.actions[0][1].command.argv[:3]==('python','-I','-c')


def test_protocol_rejects_duplicate_keys_host_paths_and_unknown_fields():
    for text in ('{"action":"submit","action":"read"}','{"action":"submit","reward":1}'):
        with pytest.raises(ValueError):parse_action(text)
    with pytest.raises(ValueError):execution_request(parse_action('{"action":"read","path":"../secrets"}'),1.)


def test_frozen_publication_replay_does_not_generate_execute_or_grade_again(tmp_path,monkeypatch):
    from feature_rl.agents import RunPublicationFailed
    f=fixture(tmp_path,monkeypatch)
    real_put=f.store.put_artifact
    def fail(value):
        if isinstance(value,c.RolloutRecord): raise ArtifactError('diagnostic rollout publication outage')
        return real_put(value)
    monkeypatch.setattr(f.store,'put_artifact',fail)
    with pytest.raises(RunPublicationFailed) as caught:
        f.runner.run(f.task,f.policy,f.limits,invocation='publication-test')
    assert len(f.backend.calls)==len(f.grades)==1
    monkeypatch.setattr(f.store,'put_artifact',real_put)
    result=f.runner.retry_publication(caught.value)
    assert record(f,result).reward==0
    assert f.runner.recover(caught.value.claim)==result
    assert len(f.backend.calls)==len(f.grades)==1


def test_outcome_publication_replay_preserves_frozen_time(tmp_path,monkeypatch):
    from feature_rl.agents.runner import RunFreezePending
    f=fixture(tmp_path,monkeypatch)
    put=f.store.put_bytes
    def fail(payload,kind,*a,**kw):
        if kind=='m7-controller-outcome':raise ArtifactError('diagnostic controller receipt outage')
        return put(payload,kind,*a,**kw)
    monkeypatch.setattr(f.store,'put_bytes',fail)
    with pytest.raises(RunFreezePending) as caught:f.runner.run(f.task,f.policy,f.limits)
    stamp=caught.value.retained['recorded_at']
    monkeypatch.setattr(f.store,'put_bytes',put)
    r=record(f,f.runner.retry_publication(caught.value))
    assert r.provenance.created_at==stamp and len(f.grades)==len(f.backend.calls)==1


def test_group_gate_accepts_only_selected_runner_null_receipts(tmp_path,monkeypatch):
    from feature_rl.training.data import TrainingDataGate
    f=fixture(tmp_path,monkeypatch);f.backend.stale=True
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    gate=TrainingDataGate(store=f.store,admit=f.runner.lifecycle.resolve_released,grader_revision=f.runner.grader.revision,runner=f.runner)
    assert gate._episode_grade(r,r.seeds.seeds[0]) is None
    # The runner audit checks the actual assigned seed, not just the selected record.
    with pytest.raises(ValueError):gate._episode_grade(r,999)


def test_new_episode_starts_new_conversation_and_workspace(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('{"action":"submit"}','{"action":"submit"}'))
    a=record(f,f.runner.run(f.task,f.policy,f.limits))
    b=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert a.run_id!=b.run_id and len(f.handles)==2 and len(f.closes)==2
    assert f.backend.contexts[0]==f.backend.contexts[1]
    assert f.backend.calls[0][3]!=f.backend.calls[1][3]


def test_saved_submission_outage_replays_same_source_without_policy_or_worker(tmp_path,monkeypatch):
    from feature_rl.agents import RunSubmissionPending
    f=fixture(tmp_path,monkeypatch)
    original=f.runner.grader.submissions.from_saved
    def fail(*a,**k):raise ArtifactError('saved source publication outage')
    monkeypatch.setattr(f.runner.grader.submissions,'from_saved',fail)
    with pytest.raises(RunSubmissionPending) as caught:f.runner.run(f.task,f.policy,f.limits)
    assert len(f.backend.calls)==1 and not f.grades and len(f.closes)==1
    monkeypatch.setattr(f.runner.grader.submissions,'from_saved',original)
    result=f.runner.retry_publication(caught.value)
    assert record(f,result).reward==0 and len(f.backend.calls)==len(f.grades)==1
    assert f.runner.retry_publication(caught.value)==result


def test_grade_publication_replay_preserves_single_measured_attempt(tmp_path,monkeypatch):
    from feature_rl.agents import RunGradePending
    f=fixture(tmp_path,monkeypatch)
    put=f.store.put_bytes
    def fail(payload,kind,*a,**kw):
        if kind=='m4-grade-receipt':raise ArtifactError('M4 publication unavailable')
        return put(payload,kind,*a,**kw)
    monkeypatch.setattr(f.store,'put_bytes',fail)
    with pytest.raises(RunGradePending) as caught:f.runner.run(f.task,f.policy,f.limits,case_seed=81)
    assert len(f.backend.calls)==len(f.grades)==1
    monkeypatch.setattr(f.store,'put_bytes',put)
    result=f.runner.retry_publication(caught.value)
    assert record(f,result).reward==0
    assert f.runner.retry_publication(caught.value)==result
    assert len(f.backend.calls)==len(f.grades)==1


def test_infrastructure_grade_retries_are_bounded_same_source_and_cases(tmp_path,monkeypatch):
    from feature_rl.grading.models import GradeReceipt
    from datetime import datetime,timezone
    f=fixture(tmp_path,monkeypatch)
    calls=[]
    def unavailable(task,submission,seed):
        calls.append((task,submission,seed))
        receipt=GradeReceipt(version='m4-grade-v1',task=task,submission=submission,verifier=None,case_seed=seed,
            manifest=None,source=None,disposition=c.Disposition.INFRASTRUCTURE,reward=None,reason='diagnostic outage',
            expected_case_ids=(),cases=(),build_evidence=None,runtime_evidence=(),cleanup_verified=True,
            implementation_revision=f.runner.grader.revision,recorded_at=datetime.now(timezone.utc))
        return f.runner.grader._publish(receipt,(f.store.get_artifact(task).costs[0],),'source_inspection')
    monkeypatch.setattr(f.runner.grader,'grade',unavailable)
    r=record(f,f.runner.run(f.task,f.policy,f.limits,case_seed=82))
    assert len(calls)==3 and len(set(calls))==1 and len(f.backend.calls)==1
    assert r.reward is None and r.disposition==c.Disposition.INFRASTRUCTURE
    assert len([e for e in r.grading_evidence if e.producer=='feature_rl.grading'])==1
    outcome=f.runner.validate_record(r)
    assert len([ref for ref in outcome.evidence if ref.kind=='m4-grade-receipt'])==3
    assert len([o for o in f.registry.accounting(r.run_id).observations if o.observation.source=='m7-grade'])==3


def test_nontraining_greedy_policy_does_not_publish_raw_scores_as_behavior(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    policy=f.policy.model_copy(update={'temperature':0.,'require_token_probabilities':False})
    r=record(f,f.runner.run(f.task,policy,f.limits))
    assert r.reward==0 and not r.training_eligible and r.steps[0].token_trace is None


def test_runtime_infrastructure_outcome_retains_sampled_step_and_skips_grading(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('{"action":"command","argv":["true"]}',))
    execute=f.runner.runtime.execute_development
    def infrastructure(handle,request):
        return execute(handle,request).model_copy(update={'reason':'infrastructure_failure',
            'failure_category':'infrastructure','cleanup_verified':False})
    monkeypatch.setattr(f.runner.runtime,'execute_development',infrastructure)
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert r.reward is None and r.disposition==c.Disposition.INFRASTRUCTURE
    assert len(r.steps)==1 and not f.grades and len(f.backend.calls)==1
    assert not f.runner.validate_record(r).cleanup_verified


def test_phase_costs_bind_actual_published_generation_and_grade(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    observations=f.registry.accounting(r.run_id).observations
    generated=next(x for x in observations if x.observation.source=='m7-generation')
    graded=next(x for x in observations if x.observation.source=='m7-grade')
    assert any(x.kind=='m7-generation' for x in generated.observation.receipts)
    assert any(x.kind=='m4-grade-receipt' for x in graded.observation.receipts)


def test_next_command_timeout_is_capped_by_remaining_measured_cpu(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,('{"action":"command","argv":["true"]}',)*2)
    execute=f.runner.runtime.execute_development
    def expensive(handle,request):
        value=execute(handle,request)
        return value.model_copy(update={'cost':value.cost.model_copy(update={'cpu_seconds':59.5})})
    monkeypatch.setattr(f.runner.runtime,'execute_development',expensive)
    r=record(f,f.runner.run(f.task,f.policy,f.limits))
    assert f.actions[1][1].command.timeout_seconds==.5
    assert f.actions[0][1].remaining_cpu_seconds==60.
    assert f.actions[1][1].remaining_cpu_seconds==.5
    assert r.stopping_reason==c.StopReason.TIME_LIMIT and len(f.backend.calls)==2
