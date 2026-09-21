"""Local registry operations over real M0 references and operation receipts."""
from __future__ import annotations

import base64
from pathlib import Path
import secrets

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, ArtifactRef, OperationResult, Visibility
from .models import (
    AccountingReport, ArtifactRecord, AttemptRecord, Claim, ClaimConflict,
    CostObservation, JobRecord, JobSpec, ObservationRecord, QuarantineNotice,
    RecoveryReport, RegistryConflict, RegistryEvent, RegistryLimit, RegistryLimits,
    StaleClaim, TraceReport, UnknownIdentity,
)
from .state import document, identity, validated
from .storage import Storage
from . import historical


def references(value):
    """Read only validated model data; opaque artifact bytes are never interpreted."""
    found = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if {'sha256', 'kind', 'schema_version', 'visibility', 'encoding'} <= item.keys():
                found.append(validated(ArtifactRef, item))
            else:
                pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return tuple(found)


class Registry:
    """Durable bookkeeping, not a factory, executor, reward source or admission gate.

    The store and registry must stay controller-private. An I/O failure can occur
    after SQLite commits; recover and query the stable identity before dispatching
    any new work. Claim tokens are local application capabilities, not human identity.
    """
    def __init__(self, root: Path, store: ArtifactStore, *, limits: RegistryLimits | None = None):
        if not isinstance(store, ArtifactStore) or store.role != ActorRole.CONTROLLER:
            raise TypeError('registry requires an M0 controller ArtifactStore')
        self.store = store
        limits = RegistryLimits() if limits is None else validated(RegistryLimits, limits)
        self._storage = Storage(root, limits, store.root)
        self.root = self._storage.root

    @property
    def limits(self):
        return self._storage.limits

    def expand_limits(self, limits: RegistryLimits, *, reason: str) -> RegistryLimits:
        """Audit monotonic storage growth without changing jobs or execution budgets.

        Existing clients adopt this exact verified event chain on their next
        locked operation. A failed reply may follow commit: recover/query before
        retrying; requesting the already-current capacity is an idempotent no-op.
        """
        return self._storage.expand_limits(validated(RegistryLimits, limits), reason)

    def _ref(self, ref):
        if not isinstance(ref, ArtifactRef):
            raise TypeError('expected an M0 ArtifactRef')
        if len(ref.kind) > 256:
            raise RegistryLimit('artifact metadata exceeds registry bound')
        return validated(ArtifactRef, ref)

    def _refs(self, values):
        if type(values) is not tuple:
            raise TypeError('references must be a tuple')
        # The exact active closure bound is enforced after the storage lock has
        # adopted any audited expansion made by another controller process.
        if len(values) > 10000:
            raise RegistryLimit('reference count exceeds closure bound')
        return tuple(self._ref(ref) for ref in values)

    def _verify(self, state, roots, explicit=None):
        roots = self._refs(roots)
        explicit = {} if explicit is None else explicit
        pending, seen, records, used = list(roots), {}, {}, 0
        while pending:
            ref = self._ref(pending.pop())
            if ref.sha256 in seen:
                if seen[ref.sha256] != ref:
                    raise RegistryConflict('one digest has conflicting reference metadata')
                continue
            if len(seen) >= self.limits.max_closure_artifacts:
                raise RegistryLimit('artifact dependency closure count exceeded')
            seen[ref.sha256] = ref
            remaining = self.limits.max_closure_bytes - used
            cap = min(remaining, self.limits.max_artifact_envelope_bytes)
            if cap <= 0:
                raise RegistryLimit('artifact dependency closure byte budget exhausted')
            if ref.encoding == 'bytes':
                payload = self.store.get_bytes(ref, max_envelope_bytes=cap,
                                               max_payload_bytes=self.limits.max_artifact_payload_bytes)
                envelope_payload = base64.b64encode(payload).decode('ascii')
                intrinsic = historical.intrinsic(ref,payload)
            else:
                artifact = self.store.get_artifact(ref, max_envelope_bytes=cap)
                envelope_payload = document(artifact)
                intrinsic = references(envelope_payload)
            used += len(canonical_json(ref.model_dump(mode='json', exclude={'sha256'}) | {'payload': envelope_payload}))
            if used > self.limits.max_closure_bytes:
                raise RegistryLimit('artifact dependency closure byte budget exceeded')
            old = state.artifacts.get(ref.sha256)
            declared = explicit.get(ref.sha256, old.dependencies if old is not None else ())
            if ref.kind in historical.RESERVED and not set(declared)<=set(intrinsic):
                raise RegistryConflict('reserved audit scope cannot add undeclared dependencies')
            dependencies = tuple(sorted(set((*intrinsic, *declared)), key=lambda r: (r.sha256, r.kind, r.visibility.value)))
            if len(dependencies) > self.limits.max_closure_artifacts:
                raise RegistryLimit('artifact dependency count exceeded')
            records[ref.sha256] = ArtifactRecord(ref=ref, dependencies=dependencies)
            pending.extend(dependencies)
        return [document(records[key]) for key in sorted(records)]

    def register(self, ref: ArtifactRef, *, dependencies: tuple[ArtifactRef, ...] = ()) -> ArtifactRecord:
        ref, dependencies = self._ref(ref), self._refs(dependencies)
        if len(set(dependencies)) != len(dependencies):
            raise RegistryConflict('duplicate explicit dependency')
        state = self._storage.change(lambda current: ('register', 'register/' + ref.sha256,
                                      {'artifacts': self._verify(current, (ref,), {ref.sha256: dependencies})}))
        return state.artifacts[ref.sha256]

    def enqueue(self, spec: JobSpec) -> JobRecord:
        spec = validated(JobSpec, spec)
        key = identity(spec)
        state = self._storage.change(lambda current: ('enqueue', 'enqueue/' + key,
                                      {'artifacts': self._verify(current, (*spec.inputs, spec.configuration)), 'spec': document(spec)}))
        return state.jobs[key]

    def historical_audit_configuration(self, configuration: ArtifactRef, *,
            subjects: tuple[ArtifactRef,...], protected: tuple[ArtifactRef,...]) -> ArtifactRef:
        """Freeze explicit historical subjects and current protected audit inputs.

        Use the returned configuration only for actual historical audit jobs.
        It permits no task/reward admission and skips no artifact-integrity or
        external human-trust check. Old JobSpec records/identities are unchanged.
        """
        configuration=self._ref(configuration)
        subject_group=historical.AuditReferences(references=self._refs(subjects))
        protected_group=historical.AuditReferences(references=self._refs(protected))
        if configuration.kind in historical.RESERVED:
            raise RegistryConflict('historical audit configuration cannot wrap another scope')
        subject_ref=self.store.put_bytes(canonical_json(document(subject_group)),historical.SUBJECTS,Visibility.PRIVATE)
        protected_ref=self.store.put_bytes(canonical_json(document(protected_group)),historical.PROTECTED,Visibility.PRIVATE)
        policy=historical.AuditPolicy(configuration=configuration,subjects=subject_ref,protected=protected_ref)
        reference=self.store.put_bytes(canonical_json(document(policy)),historical.POLICY,Visibility.PRIVATE)
        self.register(reference)  # Reserved records have strictly validated intrinsic refs.
        return reference

    def claim(self, job_id: str, *, owner: str, claim_key: str) -> Claim:
        def build(state):
            job = state.get_job(job_id)
            state.job_usable(job.spec,(*job.spec.inputs, job.spec.configuration))
            self._verify(state, (*job.spec.inputs, job.spec.configuration))
            existing = next((a.claim for a in state.attempts.values() if a.claim.claim_key == claim_key), None)
            if existing is not None:
                if existing.job_id != job_id or existing.owner != owner:
                    raise ClaimConflict('claim key belongs to a different owner/job')
                state.authenticate(existing, running=True)
                claim = existing
            else:
                claim = Claim(job_id=job_id, owner=owner, claim_key=claim_key,
                              attempt_id=identity({'job_id': job_id, 'owner': owner, 'claim_key': claim_key}), token=secrets.token_hex(32))
            return 'claim', 'claim/' + claim_key, {'artifacts': [], 'claim': document(claim)}
        state = self._storage.change(build)
        return next(a.claim for a in state.attempts.values() if a.claim.claim_key == claim_key)

    def abandon(self, claim: Claim, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> AttemptRecord:
        claim, evidence = validated(Claim, claim), self._refs(evidence)
        def build(state):
            state.authenticate(claim)
            return 'abandon', 'abandon/' + claim.attempt_id, {'artifacts': self._verify(state, evidence),
                                                           'claim': document(claim), 'reason': reason,
                                                           'evidence': [document(ref) for ref in evidence]}
        state = self._storage.change(build)
        return state.attempts[claim.attempt_id]

    def retry(self, job_id: str, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> JobRecord:
        evidence = self._refs(evidence)
        def build(state):
            job = state.get_job(job_id)
            return 'retry', 'retry/' + job_id + '/' + str(len(job.attempts)), {
                'artifacts': self._verify(state, (*evidence, *job.spec.inputs, job.spec.configuration)),
                'job_id': job_id, 'reason': reason, 'evidence': [document(ref) for ref in evidence]}
        return self._storage.change(build).jobs[job_id]

    def reconcile(self, claim: Claim, observation: CostObservation) -> ObservationRecord:
        claim, observation = validated(Claim, claim), validated(CostObservation, observation)
        def build(state):
            state.authenticate(claim)
            return 'reconcile', '/'.join(('reconcile', observation.source, observation.upstream_attempt_id, str(observation.revision))), {
                'artifacts': self._verify(state, observation.receipts), 'claim': document(claim), 'observation': document(observation)}
        state = self._storage.change(build)
        key = identity({'attempt_id': claim.attempt_id, 'observation': document(observation)})
        return state.observations[key]

    def complete(self, claim: Claim, result: OperationResult, *, observations: tuple[str, ...]) -> JobRecord:
        claim, result = validated(Claim, claim), validated(OperationResult, result)
        if type(observations) is not tuple or len(observations) > 100000:
            raise RegistryLimit('completion snapshot identities must be a bounded tuple')
        if len(set(observations)) != len(observations):
            raise RegistryConflict('duplicate completion accounting snapshot')
        observations = tuple(sorted(observations))
        def build(state):
            if len(observations) > state.limits.max_events:
                raise RegistryLimit('completion snapshot identities must be a bounded tuple')
            attempt, job = state.authenticate(claim)
            if attempt.state == 'abandoned':
                raise StaleClaim('abandoned attempt cannot finalize a job; reconcile its receipts separately')
            return 'complete', 'complete/' + job.job_id, {
                'artifacts': self._verify(state, (*references(document(result)), *job.spec.inputs, job.spec.configuration)),
                'claim': document(claim), 'result': document(result), 'observations': list(observations)}
        return self._storage.change(build).jobs[claim.job_id]

    def recover(self) -> RecoveryReport:
        return self._storage.recover()

    def job(self, job_id: str) -> JobRecord:
        def read(state, _):
            job = state.get_job(job_id)
            refs = (*job.spec.inputs, job.spec.configuration)
            if job.result is not None:
                refs += references(document(job.result))
            self._verify(state, refs)
            return job
        return self._storage.view(read)

    def attempts(self, job_id: str) -> tuple[AttemptRecord, ...]:
        return self._storage.view(lambda state, _: tuple(state.attempts[key] for key in state.get_job(job_id).attempts))

    def accounting(self, job_id: str) -> AccountingReport:
        def read(state, _):
            report = state.accounting(job_id)
            self._verify(state, tuple(ref for item in report.observations for ref in item.observation.receipts))
            return report
        return self._storage.view(read)

    def observation(self, observation_id: str) -> ObservationRecord:
        def read(state, _):
            try:
                item = state.observations[observation_id]
            except KeyError as exc:
                raise UnknownIdentity('unknown accounting snapshot') from exc
            self._verify(state, item.observation.receipts)
            return item
        return self._storage.view(read)

    def events(self, *, after: int = 0, limit: int = 100) -> tuple[RegistryEvent, ...]:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('event cursor must be nonnegative and page size 1..1000')
        return self._storage.view(lambda _, events: tuple(e for e in events if e.sequence > after)[:limit])

    def quarantine(self, ref: ArtifactRef, *, notice_id: str, reason: str, evidence: tuple[ArtifactRef, ...]) -> QuarantineNotice:
        ref, evidence = self._ref(ref), self._refs(evidence)
        notice = QuarantineNotice(notice_id=notice_id, root=ref, reason=reason, evidence=evidence,
                                  active=True, resolution_reason=None, resolution_evidence=())
        # Root bytes may already be damaged. It must have been registered earlier;
        # evidence is verified independently so corruption can still be quarantined.
        def build(state):
            state.affected(ref)
            return 'quarantine', 'quarantine/' + notice_id, {'artifacts': self._verify(state, evidence), 'notice': document(notice)}
        return self._storage.change(build).notices[notice_id]

    def lift_quarantine(self, notice_id: str, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> QuarantineNotice:
        evidence = self._refs(evidence)
        state = self._storage.change(lambda current: ('lift', 'lift/' + notice_id, {
            'artifacts': self._verify(current, evidence), 'notice_id': notice_id,
            'reason': reason, 'evidence': [document(ref) for ref in evidence]}))
        return state.notices[notice_id]

    def trace(self, ref: ArtifactRef) -> TraceReport:
        ref = self._ref(ref)
        return self._storage.view(lambda state, _: state.trace(ref))

    def assert_usable(self, ref: ArtifactRef) -> None:
        ref = self._ref(ref)
        def read(state, _):
            state.usable((ref,))
            self._verify(state, (ref,))
        self._storage.view(read)
