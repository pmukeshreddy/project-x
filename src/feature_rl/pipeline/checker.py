"""Deterministic verifier assembly from authenticated scenario authoring jobs."""
from types import SimpleNamespace
from typing import Annotated, Literal
import uuid

from pydantic import Field
from feature_rl import contracts as c
from feature_rl.registry import CostObservation, JobSpec
from feature_rl.requirements import AuthoringEvidenceResolver
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.verifiers import CheckerFinalizationInputs, CheckerFinalizer
from feature_rl.verifiers.fragments import CheckerFragmentInputs, CheckerFragmentRecord, assemble_checker
from . import authoring as a
from .construction import put, references
from .factory import FactoryRecoveryRequired
from .locking import candidate_lock
from .packaging import checked, document, read_record, typed


class CheckerAssemblyRequest(c.StrictModel):
    version: Literal['m6-checker-assembly-v1'] = 'm6-checker-assembly-v1'
    candidate: c.ArtifactRef
    inputs: CheckerFinalizationInputs
    fragments: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1, max_length=256)]


def _configuration(factory):
    return put(factory, {'version': 'm6-checker-assembly-policy-v1', 'revision': factory.revision},
        'm6-checker-assembly-policy')


def _spec(factory, ref, request):
    return JobSpec(operation='construct', inputs=(request.candidate, ref),
        configuration=_configuration(factory), implementation=factory.revision,
        invocation='m6-assemble-checker', attempt_limit=1)


def _fragments(factory, request):
    """An opaque fragment alone is not proof of accepted model authorship."""
    if len(set(request.fragments)) != len(request.fragments):
        raise ValueError('checker assembly requires distinct fragments, not duplicates')
    jobs = a.jobs(factory, request.candidate)
    if any(job.state != 'completed' for job, _ in jobs):
        raise ValueError('checker assembly requires reconciled authoring jobs')
    predecessors = {author.previous for _, author in jobs if author.previous is not None}
    selected = []
    binding = None
    for ref in request.fragments:
        matches = [(job, author) for job, author in jobs if job.result is not None
            and ref in job.result.artifacts[:-1] and job.spec.inputs[1] not in predecessors]
        if len(matches) != 1:
            raise ValueError('checker fragment lacks a unique selected terminal author')
        job, author = matches[0]
        factory.registry.assert_usable(ref)
        receipt = a.read_authoring_receipt(factory.store, job.result.artifacts[-1])
        if (author.origin != 'factory_dispatch' or not isinstance(author.call.inputs, CheckerFragmentInputs)
                or job.result.disposition != c.Disposition.SUCCESS or receipt.outputs != (ref,)
                or receipt.claim.job_id != job.job_id or receipt.request != job.spec.inputs[1]
                or receipt.revision != job.spec.implementation
                or (author.stage,author.lane) != a.stage_lane(author.call)
                or (receipt.stage,receipt.lane,receipt.repair) != (author.stage,author.lane,author.repair)
                or not any(attempt.claim == receipt.claim and attempt.state == 'completed'
                    for attempt in factory.registry.attempts(job.job_id))):
            raise ValueError('checker fragment lacks exact completed Factory authorship')
        historical = SimpleNamespace(store=factory.store, registry=factory.registry,
            authoring=a.historical_settings(factory, job), revision=job.spec.implementation)
        a.validate_call(historical, request.candidate, author.call)
        a.validate_receipt(historical, author, receipt)
        record = read_record(factory.store, ref, CheckerFragmentRecord, 'm4-checker-fragment')
        if (record.contract, record.scenario_plan, record.baseline, record.environment, record.visibility) != (
                request.inputs.contract, request.inputs.scenario_plan, request.inputs.baseline,
                request.inputs.environment, request.inputs.visibility):
            raise ValueError('checker fragment differs from assembly inputs')
        current = (author.call.source_pair, author.call.environment, author.call.resolver, author.call.sources)
        if binding is not None and binding != current:
            raise ValueError('checker fragments have different frozen source/context bindings')
        binding = current
        selected.append((ref, record, author))
    scenario_ids = [record.scenario_id for _, record, _ in selected]
    plan = typed(factory.store, request.inputs.scenario_plan, c.ScenarioPlan)
    if len(set(scenario_ids)) != len(scenario_ids) or set(scenario_ids) != {s.scenario_id for s in plan.scenarios}:
        raise ValueError('checker fragment scenario coverage is missing, duplicated, or extra')
    return selected


def _prepared(factory, request, selected, recorded_at):
    contract = typed(factory.store, request.inputs.contract, c.RequirementContract)
    plan = typed(factory.store, request.inputs.scenario_plan, c.ScenarioPlan)
    recipe = typed(factory.store, request.inputs.environment, c.EnvironmentRecipe)
    proposal = assemble_checker({record.scenario_id: record.proposal for _, record, _ in selected},
        contract=contract, plan=plan,
        timeout_seconds=min(30.0, recipe.limits.wall_seconds, contract.episode_limits.wall_seconds))
    evidence = tuple(ev for _, record, _ in selected for ev in record.provenance.evidence[-1:])
    inputs = request.inputs.model_copy(update={
        'provenance': c.Provenance(producer='feature_rl.pipeline.assemble_checker',
            producer_version=factory.revision, created_at=recorded_at,
            inputs=tuple(dict.fromkeys((*request.inputs.provenance.inputs, *request.fragments))),
            evidence=(*request.inputs.provenance.evidence, *evidence)),
        'costs': (*request.inputs.costs, *(record.costs[-1] for _, record, _ in selected))})
    author = selected[0][2]
    resolver = AuthoringEvidenceResolver(store=factory.store, **author.call.resolver.model_dump())
    return CheckerFinalizer(store=factory.store, resolver=resolver).prepare(proposal, inputs, author.call.sources)


def _claimed_at(factory, claim):
    after = 0
    while True:
        events = factory.registry.events(after=after, limit=1000)
        if not events:
            raise ValueError('checker assembly lacks a durable claim timestamp')
        for event in events:
            if event.action == 'claim' and event.data['claim'] == document(claim):
                return event.recorded_at
        after = events[-1].sequence


def assemble(factory, candidate, inputs, fragments):
    request = CheckerAssemblyRequest(candidate=checked(c.ArtifactRef, candidate),
        inputs=checked(CheckerFinalizationInputs, inputs),
        fragments=tuple(checked(c.ArtifactRef, ref) for ref in fragments))
    with candidate_lock(factory.store, request.candidate):
        selected = _fragments(factory, request)
        # Reject invalid joins before any durable assembly attempt is claimed.
        _prepared(factory, request, selected, request.inputs.provenance.created_at)
        ref = put(factory, request, 'm6-checker-assembly-request', dependencies=references(document(request)))
        job = factory.registry.enqueue(_spec(factory, ref, request))
        if job.state == 'completed':
            return job.result
        if job.state != 'queued':
            raise FactoryRecoveryRequired('recover checker assembly without reauthoring',
                factory.registry.attempts(job.job_id)[-1].claim)
        claim = factory.registry.claim(job.job_id, owner='feature_rl.pipeline.Factory', claim_key=uuid.uuid4().hex)
        return _execute(factory, claim, request, selected)


def _execute(factory, claim, request, selected):
    try:
        recorded_at = _claimed_at(factory, claim)
        ref = _prepared(factory, request, selected, recorded_at).publish(factory.store)
        a.register_output(factory, ref)
        job = factory.registry.job(claim.job_id)
        costs = (unknown_cost('construction', 'Deterministic checker assembly overhead; authoring remains in original jobs'),
            unknown_cost('storage'))
        observation = factory.registry.reconcile(claim, CostObservation(source='m6-checker-assembly',
            upstream_attempt_id=claim.attempt_id, revision=1, receipts=(job.spec.inputs[1], ref), costs=costs))
        evidence = c.EvidenceRecord(producer='feature_rl.pipeline.Factory', command=('Factory.assemble_checker',),
            recorded_at=recorded_at, exit_status=0, artifacts=(job.spec.inputs[1], ref),
            revision=factory.revision, scope='source_inspection')
        result = c.OperationResult(operation='construct', disposition=c.Disposition.SUCCESS,
            artifacts=(ref,), evidence=(evidence,), costs=costs,
            reason='Validated scenario fragments deterministically assembled; semantic qualification remains required')
        return factory.registry.complete(claim, result, observations=(observation.observation_id,)).result
    except Exception as error:
        raise FactoryRecoveryRequired('recover deterministic checker assembly without provider dispatch', claim) from error


def recover(factory, claim):
    job = factory.registry.job(claim.job_id)
    request = read_record(factory.store, job.spec.inputs[1], CheckerAssemblyRequest, 'm6-checker-assembly-request')
    if job.spec != _spec(factory, job.spec.inputs[1], request):
        raise ValueError('checker assembly recovery requires its original configuration')
    if job.state == 'completed':
        return job.result
    with candidate_lock(factory.store, request.candidate):
        return _execute(factory, claim, request, _fragments(factory, request))


def selected_assembly(factory, candidate, verifier, selected_outputs):
    """Authenticate the assembled root when freezing complete construction history."""
    matches = [factory.registry.job(job_id) for job_id in factory.registry.trace(candidate).jobs]
    matches = [job for job in matches if job.spec.invocation == 'm6-assemble-checker'
        and job.spec.inputs[0] == candidate and job.state == 'completed' and job.result is not None
        and job.result.disposition == c.Disposition.SUCCESS and job.result.artifacts == (verifier,)]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('ambiguous checker assembly selection')
    job = matches[0]
    request = read_record(factory.store, job.spec.inputs[1], CheckerAssemblyRequest, 'm6-checker-assembly-request')
    if not set(request.fragments) <= selected_outputs:
        raise ValueError('assembled checker uses a nonterminal fragment')
    historical = SimpleNamespace(store=factory.store, registry=factory.registry, revision=job.spec.implementation)
    if job.spec != _spec(historical, job.spec.inputs[1], request):
        raise ValueError('checker assembly has different frozen inputs or configuration')
    attempts = [attempt for attempt in factory.registry.attempts(job.job_id) if attempt.state == 'completed']
    if len(attempts) != 1:
        raise ValueError('checker assembly lacks a unique completed attempt')
    prepared = _prepared(historical, request, _fragments(historical, request),
        _claimed_at(historical, attempts[0].claim))
    if prepared.verifier != typed(factory.store, verifier, c.VerifierBundle):
        raise ValueError('assembled verifier differs from its authenticated scenario specifications')
    return job.spec.inputs[1]
