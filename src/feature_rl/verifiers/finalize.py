"""Finalize only closed comparisons. Generated source is inert controller data."""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from feature_rl.artifacts import ArtifactStore, ArtifactNotFound, canonical_json
from feature_rl.contracts import (ActorRole, ArtifactRef, CaseDefinition, VerifierBundle,
    VerifierPermissions, WorkerAdapter, RequirementContract, ScenarioPlan, EnvironmentRecipe, Visibility)
from feature_rl.requirements import AuthoringEvidenceResolver
from feature_rl.requirements.finalize import validate_link
from feature_rl.requirements.runtime_discovery import parse_discovery
from feature_rl.submission import SubmissionService
from feature_rl.submission.source import Submission
from feature_rl.environments import SandboxPolicy
from .language import decode_json
from .loader import read_bytes
from .authoring_models import CheckerProposal, CheckerFinalizationInputs
from .loader import _artifact, validate_verifier_bundle


@dataclass(frozen=True)
class PreparedChecker:
    """Exact CAS writes and M0 model retained for publication-only retry."""
    writes: tuple[tuple[bytes, str, Visibility, ArtifactRef], ...]
    verifier: VerifierBundle

    def publish(self, store: ArtifactStore) -> ArtifactRef:
        for data, kind, visibility, expected in self.writes:
            if store.put_bytes(data, kind, visibility) != expected:
                raise ValueError('checker publication changed immutable component identity')
        return store.put_artifact(self.verifier)


class _StagedReads:
    def __init__(self, staging, store):
        self.staging, self.store, self.writes = staging, store, []

    def put_bytes(self, data, kind, visibility):
        ref = self.staging.put_bytes(data, kind, visibility)
        self.writes.append((data, kind, visibility, ref))
        return ref

    def get_bytes(self, ref, **limits):
        try:
            return self.staging.get_bytes(ref, **limits)
        except ArtifactNotFound:
            return self.store.get_bytes(ref, **limits)

    def get_artifact(self, ref, **limits):
        return self.store.get_artifact(ref, **limits)


def resolve_checker_inputs(store, resolver, inputs, sources):
    """Reconstruct exact B-only evidence and freeze both upstream typed joins."""
    if not isinstance(resolver, AuthoringEvidenceResolver) or resolver.store.root != store.root:
        raise TypeError('checker requires the actual same-store M2 evidence resolver')
    inputs = CheckerFinalizationInputs.model_validate(inputs)
    sources = tuple(resolver.resolve(sources))
    contract = _artifact(store, inputs.contract, RequirementContract)
    plan = _artifact(store, inputs.scenario_plan, ScenarioPlan)
    if plan.contract != inputs.contract or inputs.contract not in plan.provenance.inputs:
        raise ValueError('scenario does not bind the exact frozen contract')
    expected = {ref for ref in contract.provenance.inputs if ref.kind in {
        'authoring-request', 'source-archive', 'click-runtime-discovery', 'runtime-discovery'}} | set(contract.public_checks)
    if {source.source for source in sources} != expected:
        raise ValueError('checker evidence differs from frozen contract inputs')
    if resolver.baseline != inputs.baseline or set(resolver.public_checks) != set(contract.public_checks):
        raise ValueError('checker baseline/public checks differ from evidence resolver')
    requests = [source for source in sources if source.role == 'request']
    if len(requests) != 1 or requests[0].text != contract.visible_request or requests[0].provenance_label != contract.provenance_label:
        raise ValueError('checker request differs from frozen contract')
    validate_discovery_environment(sources, inputs.environment)
    if not expected <= set(inputs.provenance.inputs):
        raise ValueError('checker provenance omits admitted evidence')
    if not set(inputs.public_examples) <= set(contract.public_checks):
        raise ValueError('public examples must be declared contract checks')
    for requirement in contract.requirements + contract.compatibility_obligations:
        for link in requirement.evidence:
            validate_link(link, sources)
    for scenario in plan.scenarios:
        validate_link(scenario.oracle_origin, sources)
    return inputs, sources, contract, plan


def validate_discovery_environment(sources, environment):
    discoveries = [source for source in sources if source.source.kind in {'click-runtime-discovery', 'runtime-discovery'}]
    if len(discoveries) != 1 or parse_discovery(discoveries[0].source, discoveries[0].text).recipe != environment:
        raise ValueError('runtime discovery does not bind exact checker/control environment')


def submission_service(store, environment):
    recipe = _artifact(store, environment, EnvironmentRecipe)
    policies = [ref for ref in recipe.provenance.inputs if ref.kind == 'sandbox-policy']
    if len(policies) != 1:
        raise ValueError('recipe must bind exactly one M3 sandbox policy')
    raw = read_bytes(store, policies[0], 65536, 'sandbox-policy')
    policy = SandboxPolicy.model_validate_json(canonical_json(decode_json(raw, 65536)))
    return SubmissionService(store=store, policy=policy)


def validate_control_submission(service, ref, baseline):
    # Deliberately malformed archive controls must reach grading. Their outer
    # manifest and exact B binding are still controller validated here.
    raw = read_bytes(service.store, ref, 65536, 'm4-submission', private=True)
    manifest = Submission.model_validate_json(canonical_json(decode_json(raw, 65536)))
    if manifest.baseline != baseline:
        raise ValueError('control submission baseline mismatch')
    read_bytes(service.store, manifest.changes, service.policy.max_archive_bytes,
        'm4-source-delta', private=True)
    return manifest


class CheckerFinalizer:
    def __init__(self, *, store, resolver):
        if not isinstance(store, ArtifactStore) or store.role is not ActorRole.CONTROLLER:
            raise TypeError('checker finalizer requires a controller store')
        self.store, self.resolver = store, resolver

    def attach_controls(self, verifier_ref, control_record_refs, *, baseline, environment):
        """Return a new frozen checker version; no checker generation or worker call."""
        from datetime import datetime, timezone
        from .control_authoring import ControlRecord
        from .loader import read_local
        verifier = _artifact(self.store, verifier_ref, VerifierBundle)
        contract, *_ = validate_verifier_bundle(self.store, verifier, contract_ref=verifier.contract,
            environment=environment, baseline=baseline)
        refs = tuple(ArtifactRef.model_validate(ref) for ref in control_record_refs)
        if not refs or len(refs) > 128 or len(set(refs)) != len(refs):
            raise ValueError('control attachment requires distinct bounded record refs')
        records = tuple(read_local(self.store, ref, ControlRecord, 'm4-control-record',
            cap=2*1024*1024) for ref in refs)
        service = submission_service(self.store, environment)
        controls = list(verifier.controls)
        known = {r.requirement_id for r in contract.requirements + contract.compatibility_obligations}
        for record in records:
            if (record.baseline, record.contract, record.environment) != (baseline, verifier.contract, environment):
                raise ValueError('control record/checker frozen join mismatch')
            targets = record.control.requirement_ids
            if len(set(targets)) != len(targets) or not set(targets) <= known:
                raise ValueError('control targets unknown/duplicate requirements')
            validate_control_submission(service, record.control.patch, baseline)
            controls.append(record.control)
        if len(controls)>128 or len({control.control_id for control in controls})!=len(controls):
            raise ValueError('duplicate or excessive attached controls')
        provenance = verifier.provenance.model_copy(update={
            'producer':'feature_rl.verifiers.CheckerFinalizer.attach_controls',
            'created_at':datetime.now(timezone.utc),
            'inputs':verifier.provenance.inputs+(verifier_ref,)+refs})
        updated = VerifierBundle.model_validate(verifier.model_copy(update={
            'controls':tuple(controls), 'provenance':provenance}))
        if len(canonical_json(updated.model_dump(mode='json'))) > 900000:
            raise ValueError('verifier exceeds bounded typed artifact size')
        return PreparedChecker((), updated)

    def prepare(self, proposal, inputs, sources) -> PreparedChecker:
        proposal = CheckerProposal.model_validate(proposal)
        inputs, sources, contract, plan = resolve_checker_inputs(self.store, self.resolver, inputs, sources)
        known = {r.requirement_id for r in contract.requirements + contract.compatibility_obligations}
        submission = submission_service(self.store, inputs.environment)
        # Validate B and source-only control joins without executing any source.
        submission.source(inputs.baseline)
        for control in inputs.controls:
            if len(set(control.requirement_ids)) != len(control.requirement_ids) or not set(control.requirement_ids) <= known:
                raise ValueError('control targets unknown/duplicate requirements')
            validate_control_submission(submission, control.patch, inputs.baseline)
        with TemporaryDirectory(prefix='m4-checker-', dir=self.store.root) as directory:
            staged = _StagedReads(ArtifactStore(Path(directory), ActorRole.CONTROLLER), self.store)
            adapter_ref = staged.put_bytes(proposal.worker_adapter.source.encode(), 'm4-worker-adapter', inputs.visibility)
            cases = tuple(CaseDefinition(case_id=case.case_id, requirement_ids=case.requirement_ids,
                mandatory=case.mandatory,
                inputs=staged.put_bytes(canonical_json(case.inputs.model_dump(mode='json')), 'm4-case-input', inputs.visibility),
                comparison=staged.put_bytes(canonical_json(case.comparison.model_dump(mode='json')), 'm4-case-comparison', inputs.visibility)) for case in proposal.cases)
            verifier = VerifierBundle(kind='VerifierBundle', schema_version=1, visibility=inputs.visibility,
                provenance=inputs.provenance, costs=inputs.costs, contract=inputs.contract,
                scenario_plan=inputs.scenario_plan, cases=cases,
                completion_manifest=tuple(case.case_id for case in cases),
                worker_adapter=WorkerAdapter(code=adapter_ref, version='m4-worker-v1',
                    supported_observables=proposal.worker_adapter.supported_observables,
                    limitations=proposal.worker_adapter.limitations), public_examples=inputs.public_examples,
                controls=inputs.controls, permissions=VerifierPermissions(controller_role=ActorRole.CONTROLLER,
                    worker_inputs=(adapter_ref,), output_limit_bytes=inputs.output_limit_bytes,
                    submission_policy=contract.allowed_changes))
            if len(canonical_json(verifier.model_dump(mode='json'))) > 900000:
                raise ValueError('verifier exceeds bounded typed artifact size')
            validate_verifier_bundle(staged, verifier, contract_ref=inputs.contract,
                environment=inputs.environment, baseline=inputs.baseline)
            return PreparedChecker(tuple(staged.writes), verifier)
