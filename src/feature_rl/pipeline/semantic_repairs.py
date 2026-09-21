"""Explicit, prospective bounded semantic repair allowance.

These repairs remain semantic attempts. The additional allowance cannot erase
old counts, authorize a different request, or admit the failed historical task.
"""
import hashlib
import json
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from .packaging import document, read_record, typed

AUTHORIZATION_KIND = 'm6-semantic-repair-authorization'


class SemanticRepairTarget(c.StrictModel):
    previous_request: c.ArtifactRef
    previous_receipt: c.ArtifactRef
    fragment: c.ArtifactRef
    replacement_call_sha256: c.Digest


class SemanticRepairAuthorization(c.StrictModel):
    version: Literal['m6-semantic-repair-authorization-v1'] = 'm6-semantic-repair-authorization-v1'
    candidate: c.ArtifactRef
    task: c.ArtifactRef
    report: c.ArtifactRef
    prior_requests: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1, max_length=256)]
    targets: Annotated[tuple[SemanticRepairTarget, ...], Field(min_length=1, max_length=3)]
    authorization: Annotated[str, Field(min_length=1, max_length=16384, pattern=r'\S')]

    @model_validator(mode='after')
    def exact_slots(self):
        if (len(set(self.prior_requests)) != len(self.prior_requests)
                or len({item.previous_request for item in self.targets}) != len(self.targets)
                or len({item.replacement_call_sha256 for item in self.targets}) != len(self.targets)):
            raise ValueError('authorization requires distinct retained predecessors and successor requests')
        return self


def call_digest(call):
    return hashlib.sha256(canonical_json(document(call))).hexdigest()


def _failed_baseline(factory, report_ref):
    """Read exact historical selection, including raw failed baseline observations."""
    from feature_rl.environments.models import Ownership
    from feature_rl.grading import read_grade
    from feature_rl.qualification.models import QualificationSummary, RunBinding
    from feature_rl.qualification.controls import assess_outcome
    from feature_rl.qualification.evidence import _assert_case_observation
    from feature_rl.verifiers import load_verifier, materialize_manifest
    from .resolver import QualificationConfiguration
    report = typed(factory.store, report_ref, c.QualificationReport)
    if (report.disposition != c.Disposition.REJECTED or report.provenance.producer != 'feature_rl.qualification'
            or len(report.provenance.inputs) != 3 or report.provenance.inputs[0] != report.task):
        raise ValueError('semantic authorization requires a retained failed qualification report')
    task, policy, summary_ref = report.provenance.inputs
    summary = read_record(factory.store, summary_ref, QualificationSummary, 'm5-qualification-summary')
    job = factory.registry.job(summary.qualification_job)
    if (job.state != 'completed' or job.result is None or job.spec.operation != 'qualify'
            or job.spec.invocation != 'm5-qualify' or job.spec.inputs != (task,)
            or job.spec.implementation != report.provenance.producer_version
            or job.result.artifacts[:2] != (report_ref, summary_ref) or job.result.disposition != report.disposition
            or (summary.task, summary.policy, summary.issues) != (task, policy, report.rejection_reasons)):
        raise ValueError('failed report is not the exact selected qualification result')
    configuration = read_record(factory.store, job.spec.configuration, QualificationConfiguration, 'm5-qualification-configuration')
    if configuration.policy != policy:
        raise ValueError('failed qualification policy mismatch')
    gate = report.baseline_absence
    if (gate is None or gate.name != 'baseline_absence' or gate.subject != task or gate.passed is not False
            or gate.disposition != c.Disposition.REJECTED or len(gate.evidence) != 1):
        raise ValueError('authorization requires the actual failed baseline gate')
    ev = gate.evidence[0]
    if (len(ev.artifacts) != 1 or ev.artifacts[0] not in summary.bindings
            or (ev.producer, ev.revision, ev.exit_status, ev.scope, ev.command) != (
                'feature_rl.qualification', job.spec.implementation, 0, 'real_integration',
                ('QualificationService.execute', 'baseline_absence', task.sha256))):
        raise ValueError('failed baseline gate lacks exact qualification evidence')
    binding = read_record(factory.store, ev.artifacts[0], RunBinding, 'm5-run-binding')
    grade_job = factory.registry.job(binding.grade_job)
    if (binding.task != task or binding.name != 'baseline_absence' or binding.mode != 'semantic_negative'
            or binding.reset is not None or binding.targets != gate.requirement_ids
            or binding.projection != summary.projection or grade_job.state != 'completed'
            or grade_job.result is None or grade_job.spec.operation != 'grade'
            or grade_job.spec.inputs != (task,) or grade_job.spec.implementation != job.spec.implementation
            or grade_job.spec.invocation != 'm5-run:'+job.job_id+':baseline_absence'
            or grade_job.result.artifacts[:1] != (binding.grade,)):
        raise ValueError('baseline grade is not the exact selected qualification run')
    run = json.loads(factory.store.get_bytes(grade_job.spec.configuration))
    if (run.get('version') != 'm5-run-configuration-v2'
            or any(run.get(key) != value for key, value in {
                'configuration': document(job.spec.configuration), 'projection': document(binding.projection),
                'submission': document(binding.submission), 'seed': binding.seed, 'name': binding.name,
                'mode': binding.mode, 'targets': list(binding.targets), 'reset': False}.items())):
        raise ValueError('baseline run configuration changed its selected identities')
    checked = load_verifier(factory.store, task)
    from feature_rl.submission.source import Submission
    from feature_rl.environments import SourceArchive
    submission = read_record(factory.store, binding.submission, Submission, 'm4-submission')
    if (submission.baseline != checked.task.baseline or submission.deletions
            or submission.changes.kind != 'm4-source-delta'
            or factory.store.get_bytes(submission.changes) != SourceArchive({}).to_tar()):
        raise ValueError('repair evidence must be an unchanged baseline submission')
    receipt = read_grade(factory.store, binding.grade)
    if ((receipt.task, receipt.submission, receipt.verifier, receipt.case_seed, receipt.implementation_revision)
            != (task, binding.submission, checked.task.private_oracle, binding.seed, configuration.grading_revision)
            or receipt.disposition != grade_job.result.disposition or not receipt.cleanup_verified
            or receipt.build_evidence is None or receipt.reward is None):
        raise ValueError('baseline grading receipt identities or measured execution mismatch')
    outcome = assess_outcome(checked, receipt, binding.mode, binding.targets, store=factory.store)
    if outcome.passed or gate.reason != outcome.detail:
        raise ValueError('retained baseline gate does not match its actual failed grade')
    manifest = materialize_manifest(checked, binding.seed)
    if (receipt.expected_case_ids != tuple(case.case_id for case in manifest.cases)
            or factory.store.get_bytes(receipt.manifest) != canonical_json(document(manifest))):
        raise ValueError('baseline manifest changed its exact frozen cases')
    compatibility = {r.requirement_id for r in checked.contract.compatibility_obligations}
    defective = set()
    for actual, case, comparison in zip(receipt.cases, manifest.cases, checked.comparisons):
        if actual.evidence is None or actual.evidence not in receipt.runtime_evidence:
            continue
        raw = json.loads(factory.store.get_bytes(actual.evidence, max_payload_bytes=32*1024*1024,
            max_envelope_bytes=48*1024*1024))
        owner = Ownership.model_validate_json(canonical_json(raw.get('record')))
        if raw.get('cleanup_verified') is not True or owner.phase != 'removed':
            raise ValueError('baseline observation cleanup is unverified')
        _assert_case_observation(checked, actual, case, comparison, raw, owner)
        if actual.status in {'protocol_failure', 'candidate_failure'} or any(
                not assertion.passed and compatibility.intersection(assertion.requirement_ids)
                for assertion in actual.assertions):
            defective.add(actual.case_id)
    return checked, defective, job.job_id


def _targets(factory, checked, defective, calls, existing):
    from . import authoring as a
    from feature_rl.verifiers.fragments import CheckerFragmentInputs
    predecessors = {request.previous for _, request in existing}
    result = []
    for call in calls:
        if not isinstance(call.inputs, CheckerFragmentInputs):
            raise ValueError('semantic authorization only permits checker fragment repairs')
        matches = [(job, old) for job, old in existing if old.lane == a.stage_lane(call)[1]
            and job.spec.inputs[1] not in predecessors]
        if len(matches) != 1:
            raise ValueError('semantic authorization requires one exact terminal predecessor')
        job, old = matches[0]
        _, receipt = a.completed_receipt(factory, job, old)
        if (receipt.disposition != c.Disposition.SUCCESS or len(receipt.outputs) != 1
                or receipt.outputs[0] not in checked.verifier.provenance.inputs
                or old.call.model_dump(exclude={'generation'}) != call.model_dump(exclude={'generation'})
                or old.call.inputs.contract != checked.task.contract
                or old.call.inputs.scenario_plan != checked.verifier.scenario_plan
                or old.call.source_pair != checked.task.source_pair
                or call.generation.diagnosis is None or call.generation.changed_input is None
                or a.semantic_request_sha256(old.call.generation.request) == a.semantic_request_sha256(call.generation.request)):
            raise ValueError('semantic authorization changed exact retained fragment or repair request')
        prefix = 'fragment_'+hashlib.sha256(call.inputs.scenario_id.encode()).hexdigest()+'_'
        if not any(case.startswith(prefix) for case in defective):
            raise ValueError('authorized fragment has no observed baseline checker defect')
        result.append(SemanticRepairTarget(previous_request=job.spec.inputs[1],
            previous_receipt=job.result.artifacts[-1], fragment=receipt.outputs[0], replacement_call_sha256=call_digest(call)))
    return tuple(result)


def create_semantic_repair_authorization(factory, *, report, calls, authorization):
    """Record explicitly requested bounded successors before any is dispatched."""
    from . import authoring as a
    from .construction import put
    from .locking import candidate_lock
    if not 1 <= len(calls) <= 3 or len({call_digest(call) for call in calls}) != len(calls):
        raise ValueError('authorization requires one to three distinct successor requests')
    checked, defective, _ = _failed_baseline(factory, report)
    candidate = typed(factory.store, checked.task.source_pair, c.SourcePair).candidate
    with candidate_lock(factory.store, candidate):
        existing = a.jobs(factory, candidate)
        if any(job.state != 'completed' for job, _ in existing):
            raise ValueError('reconcile all prior attempts before semantic authorization')
        value = SemanticRepairAuthorization(candidate=candidate, task=checked.task_ref, report=report,
            prior_requests=tuple(job.spec.inputs[1] for job, _ in existing),
            targets=_targets(factory, checked, defective, calls, existing), authorization=authorization)
        # Historical defect evidence is bound in the payload and reauthenticated
        # on every use. It is deliberately not a live dependency of the successor:
        # quarantining the old task must not quarantine its proposed repair.
        ref = put(factory, value, AUTHORIZATION_KIND, dependencies=(candidate, *value.prior_requests))
        authenticate(factory, ref, existing)
        return ref


def authenticate(factory, ref, existing):
    from . import authoring as a
    factory.registry.assert_usable(ref)
    value = read_record(factory.store, ref, SemanticRepairAuthorization, AUTHORIZATION_KIND)
    checked, defective, qualification_job = _failed_baseline(factory, value.report)
    if value.task != checked.task_ref or typed(factory.store, checked.task.source_pair, c.SourcePair).candidate != value.candidate:
        raise ValueError('semantic authorization candidate/task mismatch')
    events = []; after = 0
    while True:
        batch = factory.registry.events(after=after, limit=1000)
        if not batch: break
        events.extend(batch); after = batch[-1].sequence
    registrations = [event.sequence for event in events if event.semantic_key == 'register/'+ref.sha256]
    if len(registrations) != 1:
        raise ValueError('semantic authorization lacks its prospective Registry record')
    boundary = registrations[0]
    enqueues = {event.semantic_key.removeprefix('enqueue/'): event.sequence
        for event in events if event.action == 'enqueue'}
    completed = {event.data['claim']['job_id']: event.sequence for event in events if event.action == 'complete'}
    before = [(job, request) for job, request in existing if enqueues[job.job_id] < boundary]
    if (tuple(job.spec.inputs[1] for job, _ in before) != value.prior_requests
            or completed.get(qualification_job, boundary) >= boundary
            or any(completed.get(job.job_id, boundary) >= boundary for job, _ in before)):
        raise ValueError('semantic authorization must retain all completed prior attempts prospectively')
    by_request = {job.spec.inputs[1]: (job, request) for job, request in before}
    if any(target.previous_request not in by_request for target in value.targets):
        raise ValueError('semantic authorization predecessor is absent from retained history')
    # Authenticate targets against the selected historical task even after their
    # one authorized successor becomes the current terminal of its lane.
    for target in value.targets:
        job, old = by_request[target.previous_request]
        _, receipt = a.completed_receipt(factory, job, old)
        from feature_rl.verifiers.fragments import CheckerFragmentInputs
        if (not isinstance(old.call.inputs, CheckerFragmentInputs) or old.origin!='factory_dispatch'
                or receipt.disposition!=c.Disposition.SUCCESS
                or old.call.source_pair!=checked.task.source_pair
                or old.call.inputs.contract!=checked.task.contract
                or old.call.inputs.scenario_plan!=checked.verifier.scenario_plan
                or target.previous_request in {request.previous for _,request in before}):
            raise ValueError('semantic authorization changed its selected fragment predecessor')
        prefix = 'fragment_'+hashlib.sha256(old.call.inputs.scenario_id.encode()).hexdigest()+'_'
        if (target.previous_receipt != job.result.artifacts[-1] or receipt.outputs != (target.fragment,)
                or target.fragment not in checked.verifier.provenance.inputs
                or not any(case.startswith(prefix) for case in defective)):
            raise ValueError('semantic authorization predecessor lacks the exact observed defect')
    return value


def validate_authorized_call(value, call, previous, existing):
    from . import authoring as a
    target=next((item for item in value.targets if item.previous_request==previous),None)
    predecessors=[request for job,request in existing if job.spec.inputs[1]==previous]
    if (target is None or len(predecessors)!=1 or call_digest(call)!=target.replacement_call_sha256
            or predecessors[0].call.model_dump(exclude={'generation'})!=call.model_dump(exclude={'generation'})
            or call.generation.diagnosis is None or call.generation.changed_input is None
            or a.semantic_request_sha256(predecessors[0].call.generation.request)==a.semantic_request_sha256(call.generation.request)):
        raise ValueError('semantic authorization requires its exact diagnosed successor request')


def authorized_requests(factory, ref, existing):
    """Return only physical repairs paid by this explicit additional allowance."""
    from . import authoring as a
    value = authenticate(factory, ref, existing)
    allowed = {target.previous_request: target for target in value.targets}
    used = set(); result = {}
    for job, request in existing:
        configured = a.historical_settings(factory, job).semantic_repair_authorization
        if configured is None: continue
        target = allowed.get(request.previous)
        if (configured != ref or request.candidate != value.candidate or not request.repair
                or target is None or request.previous in used
                or call_digest(request.call) != target.replacement_call_sha256):
            raise ValueError('semantic authorization is not an exact single-use successor allowance')
        validate_authorized_call(value,request.call,request.previous,existing)
        used.add(request.previous); result[job.spec.inputs[1]] = job
    return value, result


def authenticated_allowance(history, ref, *, store, registry):
    from . import authoring as a
    refs = tuple(item for item in history.journal_refs if item.kind == AUTHORIZATION_KIND)
    if not refs and ref is None: return set()
    if ref is None or refs != (ref,) or store is None or registry is None or registry.store.root != store.root:
        raise ValueError('semantic authorization requires its exact policy/history and authentication context')
    factory = SimpleNamespace(store=store, registry=registry)
    value, selected = authorized_requests(factory, ref, a.jobs(factory, history.candidate))
    if value.candidate != history.candidate: raise ValueError('semantic authorization candidate mismatch')
    result = set()
    for request_ref, job in selected.items():
        if job.state != 'completed' or job.result is None:
            raise ValueError('authorized semantic repair has unresolved accounting')
        request = read_record(store, request_ref, a.AuthoringRequest, 'm6-authoring-request')
        _, receipt = a.completed_receipt(factory, job, request)
        receipt_ref = job.result.artifacts[-1]
        matches = [attempt for attempt in history.attempts if attempt.after == receipt_ref]
        if (len(matches) != 1 or matches[0].stage != 'verifier'
                or matches[0].costs != receipt.costs or matches[0].evidence != job.result.evidence
                or matches[0].diagnosis != request.call.generation.diagnosis
                or matches[0].change != request.call.generation.changed_input
                or not {request_ref, receipt_ref, *receipt.journal_refs} <= set(history.journal_refs)):
            raise ValueError('semantic authorization omits or changes its retained physical repair')
        result.add(receipt_ref)
    return result
