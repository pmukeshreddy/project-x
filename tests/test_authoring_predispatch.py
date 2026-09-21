"""Synthetic controller failures; no model, environment, or external process."""
import pytest

from feature_rl import contracts as c
from feature_rl.pipeline import authoring as a
from feature_rl.pipeline.authoring_models import AuthoringRequest
from feature_rl.pipeline.construction import put, references
from feature_rl.pipeline.packaging import document
from feature_rl.qualification.evidence import unknown_cost
from test_factory_authoring import setup, scenario_call


def pending_scenario(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    corrected = scenario_call(factory, base, runner)
    invalid = corrected.model_copy(update={'inputs': corrected.inputs.model_copy(update={
        'supported_observables': (*corrected.inputs.supported_observables, 'TEST unrelated observable')})})
    a.validate_call(factory, candidate, corrected)
    source = factory.screen_source(candidate)
    front = a.frontier(factory, candidate, source.artifacts[0], invalid)
    stage, lane = a.stage_lane(invalid)
    request = AuthoringRequest(candidate=candidate, frontier=front, call=invalid,
        previous=None, repair=False, stage=stage, lane=lane)
    ref = put(factory, request, 'm6-authoring-request', dependencies=references(document(request)))
    job = factory.registry.enqueue(a.spec(factory, ref, request))
    claim = factory.registry.claim(job.job_id, owner='TEST-legacy-controller', claim_key='predispatch')
    a.observe(factory, claim, 'm6-generation', (ref,), tuple(
        unknown_cost(category) for category in ('authoring', 'construction', 'storage')))
    return factory, candidate, corrected, invalid, request, claim, runner


def test_scenario_observable_mismatch_stops_before_enqueue(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    call = scenario_call(factory, base, runner)
    invalid = call.model_copy(update={'inputs': call.inputs.model_copy(update={
        'supported_observables': (*call.inputs.supported_observables, 'TEST unrelated observable')})})
    with pytest.raises(ValueError, match='observables'):
        factory.author(candidate, call=invalid)
    assert not a.jobs(factory, candidate)
    assert not runner.calls


def test_legacy_predispatch_failure_is_retained_and_corrected_without_model_repair(tmp_path, monkeypatch):
    factory, candidate, corrected, _, request, claim, runner = pending_scenario(tmp_path, monkeypatch)
    before = a.observation(factory, claim, 'm6-generation').costs
    invalid = factory.recover(claim)
    assert invalid.disposition == c.Disposition.INVALID
    assert invalid.costs == before
    receipt = a.read_authoring_receipt(factory.store, invalid.artifacts[-1])
    assert not receipt.outputs and not receipt.journal_refs and not runner.calls
    assert factory.recover(claim) == invalid
    result = factory.author(candidate, call=corrected)
    assert result.disposition == c.Disposition.SUCCESS
    jobs = a.jobs(factory, candidate)
    replacement = next(old for job, old in jobs if job.result == result)
    assert replacement.previous == receipt.request
    assert not replacement.repair
    assert replacement.call.generation == request.call.generation
    assert len(runner.calls) == 1


def test_predispatch_recovery_refuses_any_provider_receipt(tmp_path, monkeypatch):
    factory, _, _, _, _, claim, runner = pending_scenario(tmp_path, monkeypatch)
    marker = factory.store.put_bytes(b'TEST contradictory dispatch evidence', 'generation-attempt', c.Visibility.PRIVATE)
    factory.registry.register(marker)
    snapshot = a.observation(factory, claim, 'm6-generation')
    a.observe(factory, claim, 'm6-generation', (marker,), snapshot.costs)
    from feature_rl.pipeline import FactoryRecoveryRequired
    with pytest.raises(FactoryRecoveryRequired, match='provider|dispatch'):
        factory.recover(claim)
    assert not runner.calls
    assert factory.registry.job(claim.job_id).state == 'running'


def test_corrective_replacement_cannot_change_the_model_request(tmp_path, monkeypatch):
    factory, candidate, corrected, _, _, claim, runner = pending_scenario(tmp_path, monkeypatch)
    factory.recover(claim)
    changed = corrected.model_copy(update={'generation': corrected.generation.model_copy(update={
        'request': corrected.generation.request.model_copy(update={'instruction': 'TEST changed model instruction'})})})
    with pytest.raises(ValueError, match='diagnosis'):
        factory.author(candidate, call=changed)
    assert not runner.calls


def test_workflow_closes_exact_pending_controller_phase_without_dispatch(tmp_path, monkeypatch):
    import hashlib
    from feature_rl.artifacts import canonical_json
    from feature_rl.pipeline.workflow import FeatureWorkflow, FeatureRecoveryRequired
    from feature_rl.pipeline.workflow_models import FeatureStep
    from feature_rl.pipeline.packaging import read_record
    from feature_rl.registry import JobSpec

    factory, candidate, corrected, invalid, _, _, runner = pending_scenario(tmp_path, monkeypatch)
    request = factory.store.put_bytes(b'TEST workflow request', 'm6-feature-request', c.Visibility.PRIVATE)
    job = factory.registry.enqueue(JobSpec(operation='construct', inputs=(request,),
        configuration=a.configuration(factory), implementation=factory.revision, invocation='TEST-workflow', attempt_limit=1))
    claim = factory.registry.claim(job.job_id, owner='TEST-workflow', claim_key='workflow')
    workflow = object.__new__(FeatureWorkflow)
    workflow.factory, workflow.store, workflow.registry = factory, factory.store, factory.registry
    workflow._validated = lambda selected: (factory.registry.job(selected.job_id), request, None)
    key = 'author-' + hashlib.sha256(canonical_json(document(invalid))).hexdigest()
    def interrupted():
        raise RuntimeError('TEST retained predispatch controller interruption')
    with pytest.raises(FeatureRecoveryRequired):
        workflow._step(claim, request, key, {'candidate':document(candidate), 'call':document(invalid)}, interrupted, child=True)
    before = workflow._observation(claim, key).observation.costs
    workflow._recover_predispatch_phase(claim, request, factory, candidate, corrected)
    snapshot = workflow._observation(claim, key).observation
    step = read_record(factory.store, next(ref for ref in snapshot.receipts if ref.kind=='m6-feature-step'), FeatureStep, 'm6-feature-step')
    assert step.output.disposition == c.Disposition.INVALID
    assert step.key == key and snapshot.costs == before
    workflow._recover_predispatch_phase(claim, request, factory, candidate, corrected)
    assert workflow._observation(claim, key).observation == snapshot
    assert not runner.calls
