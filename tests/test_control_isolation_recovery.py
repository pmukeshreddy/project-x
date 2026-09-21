"""Actual Factory publication/accounting with explicit pre-execution diagnostics."""
from types import SimpleNamespace

import pytest

from feature_rl import contracts as c
from feature_rl.pipeline import FactoryPublicationFailed, FactoryUpstreamPending
from feature_rl.pipeline import authoring as a
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.verifiers.control_isolation import ControlIsolationExecutor
from test_factory_grade import setup as grade_setup


def executor(factory, task, submission, observe, retained):
    prepared=SimpleNamespace(writes=(), record=SimpleNamespace(control=SimpleNamespace(
        patch=submission, requirement_ids=('echo',))))
    run=ControlIsolationExecutor(factory=factory, task=task, seeds=(11,23,47),
        invocation='m6-control-isolation:'+'1'*64, observe=observe, retained=retained)
    return run,prepared


@pytest.mark.parametrize('kind', ['m4-grade-receipt','m6-grade-original'])
def test_isolation_child_publication_resumes_without_grader_redispatch(tmp_path,monkeypatch,kind):
    factory,task,submission=grade_setup(tmp_path)
    recorded=[];run,prepared=executor(factory,task,submission,recorded.append,lambda:tuple(recorded))
    put=factory.store.put_bytes
    def outage(data,name,visibility):
        if name==kind:raise OSError('Synthetic publication outage')
        return put(data,name,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    with pytest.raises((FactoryPublicationFailed,FactoryUpstreamPending)) as pending:
        run.prepare(prepared)
    monkeypatch.setattr(factory.store,'put_bytes',put)
    def forbidden(*args,**kwargs):raise AssertionError('actual grader redispatched')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    selected=factory.retry_publication(pending.value)
    # A malformed submission remains a rejected diagnostic after recovery.
    with pytest.raises(ValueError,match='declared|semantic'):
        run.prepare(prepared)
    assert recorded==[selected]
    assert selected.costs==tuple(cost for entry in factory.registry.accounting(pending.value.claim.job_id).observations
        for cost in entry.observation.costs)
    assert len(factory.registry.attempts(pending.value.claim.job_id))==1


def test_isolation_observation_failure_reuses_completed_child(tmp_path,monkeypatch):
    factory,task,submission=grade_setup(tmp_path)
    recorded=[]
    def unavailable(result):raise OSError('Synthetic parent observation outage')
    run,prepared=executor(factory,task,submission,unavailable,lambda:tuple(recorded))
    with pytest.raises(OSError):run.prepare(prepared)
    def forbidden(*args,**kwargs):raise AssertionError('actual grader redispatched')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    run.observe=recorded.append
    with pytest.raises(ValueError,match='declared|semantic'):
        run.prepare(prepared)
    assert len(recorded)==1
    jobs=[factory.registry.job(job) for job in factory.registry.trace(task).jobs
        if factory.registry.job(job).spec.operation=='grade']
    assert len(jobs)==1 and jobs[0].result==recorded[0]


def test_control_publication_retry_keeps_all_parent_cost_snapshots(tmp_path,monkeypatch):
    from test_factory_authoring import setup
    from feature_rl.pipeline.authoring_models import ControlPlan,ControlSlot
    from feature_rl.requirements import AuthoringEvidenceResolver,GenerationCandidate
    from feature_rl.verifiers import ControlFinalizationInputs,ControlProposal,SourceEdit,TextReplacement,build_control_request
    from codex_fixtures import events,response
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    inputs=ControlFinalizationInputs(control_id='SYNTHETIC',category='omission',requirement_ids=('echo',),
        expected_valid=False,expected_reason='Synthetic publication diagnostic only',baseline=base.inputs.baseline,
        contract=base.inputs.contract,environment=base.inputs.environment,provenance=base.inputs.provenance,costs=base.inputs.costs)
    resolver=AuthoringEvidenceResolver(store=factory.store,**base.resolver.model_dump())
    request=build_control_request(request_id='CONTROL',response_id='CONTROL_RESPONSE',prompt_id='CONTROL_PROMPT',
        store=factory.store,resolver=resolver,inputs=inputs,sources=base.sources,limits=base.generation.request.limits)
    proposal=ControlProposal(files=(SourceEdit(path='src/click/__init__.py',replacements=(
        TextReplacement(before='# diagnostic',after='# synthetic publication diagnostic'),)),),deletions=(),rationale='Synthetic unit fixture')
    runner.stdout=events(response(request,proposal.model_dump(mode='json')))
    call=base.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request),
        'control_plan':ControlPlan(contract=inputs.contract,slots=(ControlSlot(category='omission',requirement_ids=('echo',)),))})
    put=factory.store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m4-control-record':raise OSError('Synthetic final control publication outage')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    with pytest.raises(a.AuthoringPending) as pending:factory.author(candidate,call=call)
    monkeypatch.setattr(factory.store,'put_bytes',put)
    execution=unknown_cost('execution').model_copy(update={'wall_seconds':7.0,'measurement':'partial'})
    # Explicit synthetic ledger input isolates the accounting join independently
    # of runtime behavior; this is not an isolation qualification claim.
    a.observe(factory,pending.value.claim,'m6-control-isolation',(inputs.baseline,),
        (execution,unknown_cost('construction'),unknown_cost('storage')))
    result=factory.retry_publication(pending.value)
    receipt=a.read_authoring_receipt(factory.store,result.artifacts[-1])
    assert next(cost.wall_seconds for cost in receipt.costs if cost.category=='execution')==7.0
    assert next(cost.wall_seconds for cost in result.costs if cost.category=='execution')==7.0
    assert len(receipt.costs)==6
    assert result.costs==tuple(cost for entry in factory.registry.accounting(pending.value.claim.job_id).observations
        for cost in entry.observation.costs)
    assert factory.recover(pending.value.claim)==result
    job,request=a.validated(factory,pending.value.claim)
    _,authenticated=a.completed_receipt(factory,job,request)
    assert authenticated.costs==a.read_authoring_receipt(factory.store,result.artifacts[-1]).costs
    assert len(runner.calls)==1


@pytest.mark.parametrize('failure', ['m4-grade-receipt','m6-grade-original','parent_observe'])
def test_parent_authoring_resumes_exact_child_capability_and_archived_generation(tmp_path,monkeypatch,failure):
    from test_factory_authoring import setup
    from feature_rl.pipeline import FactoryRecoveryRequired
    from feature_rl.pipeline.authoring_models import ControlPlan,ControlSlot
    from feature_rl.requirements import AuthoringEvidenceResolver,GenerationCandidate
    from feature_rl.verifiers import ControlFinalizationInputs,ControlProposal,SourceEdit,TextReplacement,build_control_request
    from feature_rl.grading import GradingService
    from feature_rl.environments import EnvironmentRuntime,SourceRejected
    from m4_fixtures import runtime_policy
    from m5_fixtures import task_fixture
    from codex_fixtures import events,response
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    verifier=factory.author(candidate,call=base).artifacts[0]
    task=factory.store.put_artifact(factory.store.get_artifact(task_fixture(factory.store)).model_copy(update={
        'source_pair':base.source_pair,'baseline':base.inputs.baseline,'contract':base.inputs.contract,
        'environment':base.inputs.environment,'private_oracle':verifier}))
    runtime=object.__new__(EnvironmentRuntime)
    runtime.store=factory.store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy;runtime.revision='a'*40
    factory.grading=GradingService(store=factory.store,runtime=runtime,revision='b'*40)
    # Stop at the source-validation boundary. The actual M4 service still owns
    # the rejected receipt, costs and publication; no worker exists in this test.
    def reject(*args,**kwargs):raise SourceRejected('Synthetic pre-execution publication diagnostic')
    from feature_rl.submission import SubmissionService
    monkeypatch.setattr(SubmissionService,'resolve',reject)
    inputs=ControlFinalizationInputs(control_id='SYNTHETIC_ISOLATION',category='omission',requirement_ids=('echo',),
        expected_valid=False,expected_reason='Synthetic publication diagnostic only',baseline=base.inputs.baseline,
        contract=base.inputs.contract,scenario_plan=base.inputs.scenario_plan,environment=base.inputs.environment,
        provenance=base.inputs.provenance,costs=base.inputs.costs,isolation_task=task,isolation_seeds=(11,23,47))
    resolver=AuthoringEvidenceResolver(store=factory.store,**base.resolver.model_dump())
    request=build_control_request(request_id='ISOLATION',response_id='ISOLATION_RESPONSE',prompt_id='ISOLATION_PROMPT',
        store=factory.store,resolver=resolver,inputs=inputs,sources=base.sources,limits=base.generation.request.limits)
    proposal=ControlProposal(files=(SourceEdit(path='src/click/__init__.py',replacements=(
        TextReplacement(before='# diagnostic',after='# synthetic isolation publication diagnostic'),)),),deletions=(),rationale='Synthetic unit fixture')
    runner.stdout=events(response(request,proposal.model_dump(mode='json')))
    call=base.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request),
        'control_plan':ControlPlan(contract=inputs.contract,slots=(ControlSlot(category='omission',requirement_ids=('echo',)),))})
    put=factory.store.put_bytes;reconcile=factory.registry.reconcile
    def unavailable(data,kind,visibility):
        if kind==failure:raise OSError('Synthetic child publication failure')
        return put(data,kind,visibility)
    def observe_failure(claim,observation):
        if failure=='parent_observe' and observation.source=='m6-control-isolation':
            raise OSError('Synthetic parent observation failure')
        return reconcile(claim,observation)
    monkeypatch.setattr(factory.store,'put_bytes',unavailable)
    monkeypatch.setattr(factory.registry,'reconcile',observe_failure)
    with pytest.raises(FactoryRecoveryRequired) as pending:factory.author(candidate,call=call)
    monkeypatch.setattr(factory.store,'put_bytes',put)
    monkeypatch.setattr(factory.registry,'reconcile',reconcile)
    def forbidden(*args,**kwargs):raise AssertionError('grade dispatched twice')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    result=(factory.retry_publication(pending.value) if isinstance(pending.value,a.AuthoringPending)
        else factory.recover(pending.value.claim))
    assert result.disposition==c.Disposition.REJECTED
    assert not a.read_authoring_receipt(factory.store,result.artifacts[-1]).outputs
    assert len(runner.calls)==2
    child_jobs=[factory.registry.job(job) for job in factory.registry.trace(task).jobs
        if factory.registry.job(job).spec.operation=='grade']
    assert len(child_jobs)==1 and child_jobs[0].state=='completed'
    assert len(factory.registry.attempts(child_jobs[0].job_id))==1
    assert result.costs==tuple(cost for entry in factory.registry.accounting(pending.value.claim.job_id).observations
        for cost in entry.observation.costs)
    assert factory.recover(pending.value.claim)==result
