from feature_rl.pipeline.workflow import FeatureWorkflow
from feature_rl.qualification.evidence import unknown_cost
from test_registry import setup


def test_phase_completion_preserves_incurred_cost_categories(tmp_path):
    _, _, registry, source, _, spec = setup(tmp_path)
    job = registry.enqueue(spec)
    claim = registry.claim(job.job_id, owner='workflow-test', claim_key='phase')
    workflow = object.__new__(FeatureWorkflow)
    workflow.registry = registry
    workflow._observe(claim, 'intake', (source,), tuple(
        unknown_cost(category) for category in ('construction', 'discovery', 'storage')))
    completed = (unknown_cost('discovery').model_copy(update={
        'wall_seconds': 2.0, 'measurement': 'partial'}),)
    workflow._observe(claim, 'intake', (source,), completed)
    workflow._observe(claim, 'intake', (source,), completed)
    costs = registry.accounting(job.job_id).observations[-1].observation.costs
    assert {cost.category for cost in costs} == {'construction', 'discovery', 'storage'}
    assert next(cost for cost in costs if cost.category == 'discovery').wall_seconds == 2.0


def test_workflow_publication_keeps_exact_cost_records_from_each_phase(tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from feature_rl import contracts as c
    from feature_rl.artifacts import canonical_json
    from feature_rl.pipeline.packaging import document
    from feature_rl.pipeline.workflow_models import FeatureOutcome

    _, store, registry, source, _, spec = setup(tmp_path)
    job = registry.enqueue(spec)
    claim = registry.claim(job.job_id, owner='workflow-test', claim_key='publication')
    workflow = object.__new__(FeatureWorkflow)
    workflow.registry, workflow.store = registry, store
    workflow.factory = SimpleNamespace(store=store, registry=registry, revision='a' * 40)
    workflow._validated = lambda selected: (registry.job(selected.job_id), source, None)
    rejected = c.OperationResult(operation='construct', disposition=c.Disposition.REJECTED,
        artifacts=(), evidence=(), costs=(unknown_cost('construction'),), reason='Synthetic terminal child rejection')
    _, step = workflow._step(claim, source, 'construction', {},
        lambda: (rejected, (unknown_cost('construction'), unknown_cost('storage'))), child=True)
    outcome = FeatureOutcome(claim=claim, request=source, selected=rejected, steps=(step,),
        recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc), limitations=())
    payload = canonical_json(document(outcome))
    result = workflow._publish_outcome(payload, claim)
    costs = tuple(cost for row in registry.accounting(job.job_id).observations
        for cost in row.observation.costs)
    assert result.costs == costs
    assert sum(cost.category == 'construction' for cost in costs) == 2
    assert result.disposition == c.Disposition.REJECTED
    assert workflow._publish_outcome(payload, claim) == result


def test_preparation_validation_does_not_requalify_runtime(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from feature_rl import contracts as c
    from feature_rl.artifacts import canonical_json
    from feature_rl.environments import SandboxPolicy
    from feature_rl.pipeline.packaging import document
    from feature_rl.pipeline.workflow_models import IntakeSelection, PreparationSelection
    from feature_rl.registry import JobSpec
    from test_factory_authoring import setup as authoring_setup

    factory, candidate, call, runner = authoring_setup(tmp_path, monkeypatch)
    store, registry = factory.store, factory.registry
    pair = store.get_artifact(call.source_pair)
    policy = SandboxPolicy.model_validate_json(store.get_bytes(call.environment.policy))
    discovery = next(source for source in call.sources if source.source.kind == 'runtime-discovery')
    # These bytes are explicit unit diagnostics, never sandbox execution evidence.
    evidence = tuple(store.put_bytes(label, 'TEST-runtime-evidence', c.Visibility.PRIVATE)
        for label in (b'TEST synthetic build', b'TEST synthetic execution'))
    data = json.loads(discovery.text)
    data.update(build_evidence_sha256=evidence[0].sha256, execution_evidence_sha256=evidence[1].sha256)
    raw = canonical_json(data)
    context = discovery.model_copy(update={'source':store.put_bytes(raw, 'runtime-discovery', c.Visibility.AUTHORING), 'text':raw.decode()})
    selected = IntakeSelection(candidate=candidate, source_pair=call.source_pair, request=call.resolver.request,
        baseline=pair.baseline, reference=pair.reference, license_text=call.resolver.request,
        provenance_label=pair.provenance_label, mixed_paths_for_qualification=())
    prepared = PreparationSelection(environment=call.environment, context=context,
        entry_points=tuple(data['entry_points']), supported_observables=tuple(data['supported_observables']),
        private_evidence=evidence, costs=(unknown_cost('execution'),))
    job = registry.enqueue(JobSpec(operation='construct', inputs=(call.resolver.request,),
        configuration=call.environment.policy, implementation=factory.revision, invocation='TEST-preparation', attempt_limit=1))
    claim = registry.claim(job.job_id, owner='TEST-workflow', claim_key='preparation')
    workflow = object.__new__(FeatureWorkflow)
    workflow.factory, workflow.store, workflow.registry = factory, store, registry
    workflow._validated = lambda selected: (registry.job(selected.job_id), call.resolver.request, None)
    def forbid_runtime_work(*args):
        pytest.fail('immutable preparation validation must not execute runtime qualification')
    import pytest
    workflow.runtime = SimpleNamespace(base_policy=policy, bind_policy=forbid_runtime_work)
    result, _ = workflow._step(claim, call.resolver.request, 'preparation', document(selected),
        lambda:(prepared, prepared.costs))
    assert result == prepared and not runner.calls
