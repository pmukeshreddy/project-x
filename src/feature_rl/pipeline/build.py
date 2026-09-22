"""Registry-backed freezing/publication of complete BUILT roots, without execution."""
from datetime import datetime, timezone
import hashlib
import secrets
import time

from feature_rl.artifacts import ArtifactStore, ArtifactError, ArtifactNotFound, ArtifactIntegrityError, canonical_json
from feature_rl import contracts as c
from feature_rl.environments import EnvironmentError
from feature_rl.registry import Registry, Claim, CostObservation, JobSpec, RegistryError, RegistryConflict, QuarantinedError
from . import packaging
from .models import (BuildInputs, BuildIntent, FrozenBuild, FailedBuild, BuildRejected,
                     BuildRecoveryRequired, BuildPublicationFailed)


def costs(elapsed=None):
    common = dict(cpu_seconds=None, gpu_seconds=None, input_tokens=None, output_tokens=None, human_minutes=None, usd=None)
    return (
        c.CostRecord(category='construction', wall_seconds=elapsed, **common,
            measurement='unknown' if elapsed is None else 'partial',
            note='M6 input verification, inert assembly and public component publication up to freeze; no source execution'),
        c.CostRecord(category='storage', wall_seconds=None, **common, measurement='unknown',
            note='Receipt/root publication, registry commits and any recovery overhead are unmeasured, not zero'),
    )


class TaskBuilder:
    def __init__(self, *, store: ArtifactStore, registry: Registry, revision: str):
        if (not isinstance(store, ArtifactStore) or store.role != c.ActorRole.CONTROLLER
                or not isinstance(registry, Registry) or registry.store.root != store.root):
            raise TypeError('TaskBuilder requires the identical M0 controller store and registry')
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact implementation revision required')
        self.store, self.registry, self.revision = store, registry, revision
        self.configuration = canonical_json({'version': 'm6-build-policy-v1', 'revision': revision,
            'package_policy': 'complete-B-positive-projection-v1'})

    def _put(self, value, kind):
        payload = canonical_json(packaging.document(value))
        if len(payload) > packaging.MAX_DOCUMENT:
            raise BuildRejected('build record byte limit')
        return self.store.put_bytes(payload, kind, c.Visibility.PRIVATE)

    def _inputs(self, ref):
        return packaging.read_record(self.store, ref, BuildInputs, 'm6-build-inputs')

    def _claim(self, claim):
        claim = packaging.checked(Claim, claim)
        job = self.registry.job(claim.job_id)
        if not any(attempt.claim == claim for attempt in self.registry.attempts(claim.job_id)):
            raise BuildRejected('unknown/mismatched build claim')
        if (job.spec.operation != 'construct' or job.spec.implementation != self.revision or len(job.spec.inputs) != 1
                or packaging.read_bytes(self.store, job.spec.configuration, packaging.MAX_DOCUMENT, kind='m6-build-policy') != self.configuration):
            raise BuildRejected('claim does not belong to this builder configuration')
        inputs_ref = job.spec.inputs[0]
        return claim, job, inputs_ref, self._inputs(inputs_ref)

    def _snapshots(self, claim):
        return tuple(item for item in self.registry.accounting(claim.job_id).observations if item.attempt_id == claim.attempt_id)

    def _observe(self, claim, refs, values, revision):
        return self.registry.reconcile(claim, CostObservation(source='m6-build', upstream_attempt_id=claim.attempt_id,
            revision=revision, receipts=refs, costs=values))

    def job_spec(self, inputs: BuildInputs) -> JobSpec:
        """Freeze the exact input/policy CAS identity without enqueueing or assembly."""
        inputs = packaging.checked(BuildInputs, inputs)
        inputs_ref = self._put(inputs, 'm6-build-inputs')
        policy_ref = self.store.put_bytes(self.configuration, 'm6-build-policy', c.Visibility.PRIVATE)
        return JobSpec(operation='construct', inputs=(inputs_ref,), configuration=policy_ref,
            implementation=self.revision, invocation=inputs.invocation, attempt_limit=3)

    def build(self, inputs: BuildInputs, *, owner: str, claim_key: str) -> c.OperationResult:
        inputs = packaging.checked(BuildInputs, inputs)
        spec = self.job_spec(inputs)
        inputs_ref = spec.inputs[0]
        # The request is an opaque private input so a missing requested dependency
        # can still receive an attributable attempt and failed operation result.
        job = self.registry.enqueue(spec)
        if job.state == 'completed':
            return job.result
        claim = self.registry.claim(job.job_id, owner=owner, claim_key=claim_key)
        if job.state != 'queued':
            return self.recover(claim)
        intent = BuildIntent(inputs=inputs_ref, claim=claim, started_at=datetime.now(timezone.utc), nonce=secrets.token_hex(32))
        try:
            intent_ref = self._put(intent, 'm6-build-intent')
            # Competing replays have different intent bytes but the same revision
            # key. Only one wins permission to assemble under this claim.
            self._observe(claim, (intent_ref,), costs(), 1)
        except (ArtifactError, OSError, RegistryError) as exc:
            raise BuildRecoveryRequired('build intent was not confirmed; recover/query before any assembly', claim) from exc
        start = time.monotonic()
        try:
            view, package, dependencies = packaging.assemble(self.store, self.registry, inputs)
        except (ArtifactError, OSError, RegistryError, EnvironmentError, ValueError) as exc:
            if isinstance(exc, (ArtifactNotFound, QuarantinedError)):
                disposition = c.Disposition.BLOCKED
            elif isinstance(exc, ArtifactIntegrityError):
                disposition = c.Disposition.INVALID
            elif isinstance(exc, (OSError, ArtifactError, RegistryError)):
                disposition = c.Disposition.INFRASTRUCTURE
            else:
                disposition = c.Disposition.UNSUPPORTED
            failed = FailedBuild(inputs=inputs_ref, intent=intent_ref, claim=claim, recorded_at=datetime.now(timezone.utc),
                revision=self.revision, disposition=disposition, reason=(type(exc).__name__ + ': ' + str(exc))[:2048],
                costs=costs(time.monotonic() - start))
            return self._publish_receipt(failed, 'm6-failed-build')
        frozen = FrozenBuild(inputs=inputs_ref, intent=intent_ref, claim=claim, recorded_at=datetime.now(timezone.utc),
            revision=self.revision, solver_view=view, package=package, dependencies=dependencies,
            costs=costs(time.monotonic() - start))
        return self._publish_receipt(frozen, 'm6-frozen-build')

    def _publish_receipt(self, receipt, kind):
        try:
            ref = self._put(receipt, kind)
            dependencies = (receipt.intent,)
            if isinstance(receipt, FrozenBuild):
                view = receipt.solver_view
                dependencies += (*receipt.dependencies, receipt.package, view.instruction, view.workspace,
                                 view.runtime_manifest, view.inventory, *view.public_checks)
            self.registry.register(ref, dependencies=tuple(dict.fromkeys(dependencies)))
            self._observe(receipt.claim, (receipt.intent, ref), receipt.costs, 2)
        except (ArtifactError, OSError, RegistryError) as exc:
            raise BuildPublicationFailed('exact build receipt publication/reconciliation is pending', receipt.claim,
                payload=canonical_json(packaging.document(receipt)), kind=kind) from exc
        return self._finish(receipt, ref)

    def _evidence(self, receipt, ref, success):
        return c.EvidenceRecord(producer='feature_rl.pipeline.TaskBuilder', command=('TaskBuilder.build',),
            recorded_at=receipt.recorded_at, exit_status=0 if success else 1, artifacts=(ref,),
            revision=receipt.revision, scope='source_inspection')

    def _task(self, receipt, ref, inputs):
        pair = packaging.typed(self.store, inputs.source_pair, c.SourcePair)
        candidate = packaging.typed(self.store, pair.candidate, c.CandidateRecord)
        verifier = packaging.typed(self.store, inputs.verifier, c.VerifierBundle)
        return c.TaskBundle(kind='TaskBundle', schema_version=1,
            visibility=c.Visibility.EVALUATION if candidate.partition == c.Partition.LOCKED_TEST else c.Visibility.PRIVATE,
            provenance=c.Provenance(producer='feature_rl.pipeline.TaskBuilder', producer_version=self.revision,
                created_at=receipt.recorded_at, inputs=(receipt.inputs, *receipt.dependencies, receipt.package),
                evidence=(self._evidence(receipt, ref, True),)), costs=receipt.costs,
            state=c.TaskState.BUILT, qualification=None, partition=candidate.partition,
            repository_family=candidate.repository_family, request_lineage=candidate.request_lineage,
            source_pair=inputs.source_pair, baseline=pair.baseline, solver_view=receipt.solver_view,
            contract=inputs.contract, environment=inputs.environment.recipe, adapter_version='behavioral-command-v1',
            private_oracle=inputs.verifier, reference_solution=pair.reference)

    def _finish(self, receipt, ref):
        claim, job, inputs_ref, inputs = self._claim(receipt.claim)
        if receipt.inputs != inputs_ref or receipt.revision != self.revision:
            raise BuildRejected('receipt input/revision mismatch')
        if job.state == 'completed':
            return job.result
        if job.state != 'running' or job.attempts[-1] != claim.attempt_id or not any(
                item.claim == claim and item.state == 'running' for item in self.registry.attempts(claim.job_id)):
            raise BuildRejected('only the current running attempt can finish a frozen outcome')
        try:
            if isinstance(receipt, FrozenBuild):
                resolved = packaging.resolve(self.store, inputs)
                if receipt.dependencies != resolved.dependencies:
                    raise BuildRejected('frozen dependency join drift')
                for dependency in receipt.dependencies:
                    self.registry.assert_usable(dependency)
                packaging.inspect_package(self.store, inputs, receipt.solver_view, receipt.package)
                task_ref = self.store.put_artifact(self._task(receipt, ref, inputs))
                self.registry.register(task_ref)
                disposition, artifacts = c.Disposition.SUCCESS, (task_ref,)
                reason = 'Complete BUILT root and final solver bytes frozen; qualification and release remain required'
            else:
                if receipt.disposition in (c.Disposition.SUCCESS, c.Disposition.PROVISIONAL):
                    raise BuildRejected('failed receipt cannot assert successful construction')
                disposition, artifacts, reason = receipt.disposition, (ref,), receipt.reason
            snapshots = tuple(sorted(self._snapshots(claim), key=lambda item: item.observation_id))
            result = c.OperationResult(operation='construct', disposition=disposition, artifacts=artifacts,
                evidence=(self._evidence(receipt, ref, isinstance(receipt, FrozenBuild)),),
                costs=tuple(cost for item in snapshots for cost in item.observation.costs), reason=reason)
            return self.registry.complete(claim, result, observations=tuple(item.observation_id for item in snapshots)).result
        except (ArtifactError, OSError, RegistryError, EnvironmentError, ValueError) as exc:
            raise BuildRecoveryRequired('frozen outcome could not be completed; retain claim and exact reconciled receipt', claim) from exc

    def recover(self, claim: Claim) -> c.OperationResult:
        claim, job, inputs_ref, _ = self._claim(claim)
        if job.state == 'completed':
            return job.result
        snapshots = [item for item in self._snapshots(claim) if item.observation.source == 'm6-build'
                     and item.observation.upstream_attempt_id == claim.attempt_id]
        if len(snapshots) != 1 or snapshots[0].observation.revision < 2:
            raise BuildRecoveryRequired('assembly was not durably frozen; unknown attempt remains; reconcile or explicitly abandon/retry', claim)
        saved = [ref for ref in snapshots[0].observation.receipts if ref.kind in ('m6-frozen-build', 'm6-failed-build')]
        if len(saved) != 1:
            raise BuildRejected('exactly one frozen/failed outcome receipt is required')
        ref = saved[0]
        model = FrozenBuild if ref.kind == 'm6-frozen-build' else FailedBuild
        receipt = packaging.read_record(self.store, ref, model, ref.kind)
        if receipt.claim != claim or receipt.inputs != inputs_ref:
            raise BuildRejected('saved receipt claim/input mismatch')
        return self._finish(receipt, ref)

    def retry_publication(self, pending: BuildPublicationFailed) -> c.OperationResult:
        if not isinstance(pending, BuildPublicationFailed) or type(pending.payload) is not bytes or len(pending.payload) > packaging.MAX_DOCUMENT:
            raise BuildRejected('bounded pending build receipt required')
        models = {'m6-frozen-build': FrozenBuild, 'm6-failed-build': FailedBuild}
        if pending.kind not in models:
            raise BuildRejected('unsupported pending receipt kind')
        receipt = models[pending.kind].model_validate_json(pending.payload)
        if (hashlib.sha256(pending.payload).hexdigest() != pending.sha256
                or canonical_json(packaging.document(receipt)) != pending.payload or receipt.claim != pending.claim):
            raise BuildRejected('pending receipt bytes/claim mismatch')
        claim, _, inputs_ref, _ = self._claim(pending.claim)
        intent = packaging.read_record(self.store, receipt.intent, BuildIntent, 'm6-build-intent')
        if intent.claim != claim or intent.inputs != inputs_ref or receipt.inputs != inputs_ref or receipt.revision != self.revision:
            raise BuildRejected('pending receipt intent/input/revision mismatch')
        return self._publish_receipt(receipt, pending.kind)

    def solver_package(self, task_ref: c.ArtifactRef) -> bytes:
        """Inspect the already-frozen BUILT package, not qualification or release."""
        try:
            self.registry.assert_usable(task_ref)
            task = packaging.typed(self.store, task_ref, c.TaskBundle)
            if task.state != c.TaskState.BUILT or task.qualification is not None:
                raise BuildRejected('this inspection API requires the original BUILT root')
            refs = [ref for evidence in task.provenance.evidence for ref in evidence.artifacts if ref.kind == 'm6-frozen-build']
            if len(refs) != 1:
                raise BuildRejected('built root requires its exact frozen receipt')
            receipt = packaging.read_record(self.store, refs[0], FrozenBuild, 'm6-frozen-build')
            inputs = self._inputs(receipt.inputs)
            if task != self._task(receipt, refs[0], inputs):
                raise BuildRejected('built root differs from its frozen receipt')
            return packaging.inspect_package(self.store, inputs, task.solver_view, receipt.package)
        except (ArtifactError, RegistryError, EnvironmentError, ValueError) as exc:
            raise BuildRejected('solver package inspection failed: ' + str(exc)) from exc
