"""Concrete Factory operations, selected attempts and incremental accounting."""
from datetime import datetime, timezone
import hashlib
import time
import uuid
from typing import Annotated, Literal

from pydantic import Field
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.qualification.evidence import collapse_costs, unknown_cost
from feature_rl.registry import Registry, RegistryError, Claim, JobSpec, CostObservation
from .packaging import MAX_DOCUMENT, checked, document, typed, read_record
from .locking import candidate_lock
from .models import BuildInputs


class FactoryRecoveryRequired(Exception):
    def __init__(self, message, claim):
        super().__init__(message)
        self.claim = claim


class FactoryPublicationFailed(FactoryRecoveryRequired):
    def __init__(self, message, claim, payload, *, kind='m6-source-disposition'):
        super().__init__(message, claim)
        self.payload = payload
        self.sha256 = hashlib.sha256(payload).hexdigest()
        self.kind = kind


class FactoryUpstreamPending(FactoryRecoveryRequired):
    def __init__(self, message, claim, upstream):
        super().__init__(message,claim)
        self.upstream = upstream


class SourceDisposition(c.StrictModel):
    version: Literal['m6-source-disposition-v1'] = 'm6-source-disposition-v1'
    claim: Claim
    candidate: c.ArtifactRef
    repository_family: c.Identifier
    request_lineage: tuple[c.Identifier, ...]
    screening: c.ScreeningDecision
    license: c.LicenseRecord
    partition: c.Partition
    source_status: Literal['eligible', 'rejected', 'unresolved']
    disposition: c.Disposition
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    original_costs: tuple[c.CostRecord, ...]
    costs: tuple[c.CostRecord, ...]
    revision: c.Revision
    recorded_at: c.UTCDateTime


def read_source_disposition(store: ArtifactStore, reference: c.ArtifactRef) -> SourceDisposition:
    """Read exact frozen history, not current source/task admission."""
    if reference.visibility != c.Visibility.PRIVATE:
        raise ValueError('source disposition must retain private visibility')
    return read_record(store, reference, SourceDisposition, 'm6-source-disposition')


def source_decision(candidate):
    if candidate.screening.disposition == c.Disposition.REJECTED:
        return 'rejected', c.Disposition.REJECTED, candidate.screening.reason[:4096]
    if candidate.license.status == 'ineligible':
        return 'rejected', c.Disposition.REJECTED, 'Actual retained source license is ineligible'
    if candidate.screening.disposition != c.Disposition.SUCCESS:
        return 'unresolved', candidate.screening.disposition, candidate.screening.reason[:4096]
    if candidate.license.status != 'verified' or candidate.license.license_text is None:
        return 'unresolved', c.Disposition.BLOCKED, 'Source license evidence is unresolved'
    if candidate.partition == c.Partition.UNASSIGNED:
        return 'unresolved', c.Disposition.BLOCKED, 'Repository family/lineage partition is unassigned'
    return 'eligible', c.Disposition.SUCCESS, 'Frozen candidate source prerequisites are eligible; no BUILT or released task is asserted'


class Factory:
    def __init__(self, *, store: ArtifactStore, registry: Registry, revision: str, builder=None, qualification=None, authoring=None):
        if (not isinstance(store, ArtifactStore) or store.role != c.ActorRole.CONTROLLER
                or not isinstance(registry, Registry) or registry.store is not store):
            raise TypeError('Factory requires the same actual controller store and Registry')
        if type(revision) is not str or len(revision) not in (40,64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact factory implementation revision required')
        self.store, self.registry, self.revision = store, registry, revision
        from .build import TaskBuilder
        self.builder = TaskBuilder(store=store,registry=registry,revision=revision) if builder is None else builder
        if (type(self.builder) is not TaskBuilder or self.builder.store is not store or self.builder.registry is not registry):
            raise TypeError('Factory requires the actual same-store TaskBuilder')
        from feature_rl.qualification import QualificationService
        if qualification is not None and (type(qualification) is not QualificationService
                or qualification.store is not store or qualification.registry is not registry):
            raise TypeError('Factory qualification must be the actual same-store M5 service')
        self.qualification=qualification
        from .authoring_models import AuthoringSettings
        self.authoring=None if authoring is None else checked(AuthoringSettings,authoring)
        self.source_configuration = store.put_bytes(self._source_policy(revision),
            'm6-source-policy', c.Visibility.PRIVATE)
        registry.register(self.source_configuration)

    def construct(self, candidate: c.ArtifactRef, *, inputs: BuildInputs | None=None) -> c.OperationResult:
        from .construction import construct
        return construct(self,candidate,inputs)

    def author(self, candidate: c.ArtifactRef, *, call) -> c.OperationResult:
        from .authoring import author
        return author(self,candidate,call)

    def import_rejected_authoring(self, candidate: c.ArtifactRef, *, call, journal_refs) -> c.OperationResult:
        from .authoring_import import import_rejected
        return import_rejected(self,candidate,call,journal_refs)

    def qualify(self, task_ref: c.ArtifactRef, *, policy=None) -> c.OperationResult:
        from .qualification import qualify
        return qualify(self,task_ref,policy)

    def accept(self, review_request_ref: c.ArtifactRef, attestation_ref: c.ArtifactRef) -> c.OperationResult:
        from .qualification import accept
        return accept(self,review_request_ref,attestation_ref)

    def release(self, task_ref: c.ArtifactRef, *, accepted_report: c.ArtifactRef | None=None) -> c.OperationResult:
        from .qualification import release
        return release(self,task_ref,accepted_report)

    @staticmethod
    def _source_policy(revision):
        return canonical_json({'version':'m6-source-policy-v1', 'revision':revision,
            'screening':'retain-exact-M0-disposition', 'license':'verified',
            'partition':'assigned', 'source_costs':'one-candidate-source-admission'})

    def _source_spec(self, candidate):
        return JobSpec(operation='construct', inputs=(candidate,), configuration=self.source_configuration,
            implementation=self.revision, invocation='m6-source-admission', attempt_limit=3)

    def _observe_source(self, claim, receipts, costs, revision):
        return self.registry.reconcile(claim, CostObservation(source='m6-source',
            upstream_attempt_id=claim.attempt_id, revision=revision, receipts=receipts, costs=costs))

    def screen_source(self, candidate: c.ArtifactRef) -> c.OperationResult:
        """Retain the actual M1/M0 source disposition before construction.

        This imports the candidate's original aggregate costs exactly once for
        this selected source job, retaining the originals rather than inventing
        individual provider attempts. Downstream construction references this
        job and must not add its costs as newly incurred work.
        """
        candidate = checked(c.ArtifactRef, candidate)
        with candidate_lock(self.store,candidate):
            return self._screen_source(candidate)

    def _screen_source(self, candidate):
        value = typed(self.store, candidate, c.CandidateRecord)
        original = {'screening': document(value.screening), 'license':document(value.license),
            'costs':[document(cost) for cost in value.costs]}
        if len(canonical_json(original)) > MAX_DOCUMENT // 2 or len(value.costs) > 256:
            raise ValueError('source metadata/cost snapshot exceeds bounded Factory admission input')
        # The candidate is immutable. Reusing a newer Factory version must not
        # import its already-incurred original costs a second time. The same
        # candidate lock serializes this lookup with claim/freeze/completion.
        try: jobs = tuple(self.registry.job(j) for j in self.registry.trace(candidate).jobs)
        except RegistryError as exc:
            from feature_rl.registry import UnknownIdentity
            if not isinstance(exc,UnknownIdentity): raise
            jobs = ()
        source_jobs = [j for j in jobs if j.spec.operation=='construct'
            and j.spec.invocation=='m6-source-admission' and j.spec.inputs==(candidate,)]
        if len(source_jobs)>1: raise ValueError('ambiguous historical source imports require reconciliation')
        if (source_jobs and source_jobs[0].state=='queued'
                and source_jobs[0].spec==self._source_spec(candidate)
                and not self.registry.attempts(source_jobs[0].job_id)):
            source_jobs=[]  # No execution permission/attempt was ever issued.
        if source_jobs:
            prior=source_jobs[0]
            if prior.state!='completed':
                attempts=self.registry.attempts(prior.job_id)
                if not attempts: raise RegistryError('source job is queued; resume its original Factory configuration')
                raise FactoryRecoveryRequired('source import exists; recover its original attempt/configuration',attempts[-1].claim)
            if prior.result is None or len(prior.result.artifacts)!=1:
                raise ValueError('invalid selected source result')
            record=read_source_disposition(self.store,prior.result.artifacts[0])
            policy=self.store.get_bytes(prior.spec.configuration,max_envelope_bytes=16384,max_payload_bytes=8192)
            if (prior.spec.configuration.kind!='m6-source-policy'
                    or policy!=self._source_policy(record.revision) or prior.spec.implementation!=record.revision
                    or record.claim.job_id!=prior.job_id or record.candidate!=candidate
                    or not any(a.claim==record.claim and a.state=='completed' for a in self.registry.attempts(prior.job_id))
                    or prior.result.disposition!=record.disposition or prior.result.costs!=record.costs):
                raise ValueError('historical source import lacks exact selected policy/claim/result')
            self._validate_source_value(record,value)
            return prior.result
        job = self.registry.enqueue(self._source_spec(candidate))
        if job.state == 'completed': return job.result  # historical result; consumers recheck usable refs
        if job.state != 'queued':
            raise FactoryRecoveryRequired('source attempt already exists; recover without rescreening',
                self.registry.attempts(job.job_id)[-1].claim)
        claim = self.registry.claim(job.job_id, owner='feature_rl.pipeline.Factory', claim_key=uuid.uuid4().hex)
        categories = sorted({cost.category for cost in value.costs} | {'construction', 'storage'})
        try:
            self._observe_source(claim, (self.source_configuration,candidate),
                tuple(unknown_cost(cat,'Source import/controller attempt not frozen; incurred work remains unknown') for cat in categories), 1)
        except (ArtifactError, RegistryError, OSError) as exc:
            raise FactoryRecoveryRequired('source intent unconfirmed; do not redispatch', claim) from exc
        started = time.monotonic()
        status, disposition, reason = source_decision(value)
        controller = c.CostRecord(category='construction', wall_seconds=time.monotonic()-started,
            cpu_seconds=None,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,
            usd=None,measurement='partial',note='Source decision controller wall; original source cost records retained separately')
        costs = collapse_costs((*value.costs,controller,unknown_cost('storage','Source publication and Registry overhead unmeasured')))
        receipt = SourceDisposition(claim=claim,candidate=candidate,repository_family=value.repository_family,
            request_lineage=value.request_lineage,screening=value.screening,license=value.license,partition=value.partition,
            source_status=status,disposition=disposition,reason=reason[:4096],original_costs=value.costs,costs=costs,
            revision=self.revision,recorded_at=datetime.now(timezone.utc))
        return self._publish_source(canonical_json(document(receipt)), claim)

    def _source_record(self, payload, claim):
        if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:
            raise ValueError('source receipt exceeds reader bound')
        receipt = SourceDisposition.model_validate_json(payload)
        if canonical_json(document(receipt)) != payload or receipt.claim != claim or receipt.revision != self.revision:
            raise ValueError('source receipt claim/configuration mismatch')
        job = self.registry.job(claim.job_id)
        if job.spec != self._source_spec(receipt.candidate) or not any(a.claim==claim for a in self.registry.attempts(job.job_id)):
            raise ValueError('source receipt lacks actual selected job/attempt')
        value = typed(self.store, receipt.candidate, c.CandidateRecord)
        self._validate_source_value(receipt,value)
        return receipt

    @staticmethod
    def _validate_source_value(receipt,value):
        if ((receipt.repository_family,receipt.request_lineage,receipt.screening,receipt.license,receipt.partition,receipt.original_costs)
                != (value.repository_family,value.request_lineage,value.screening,value.license,value.partition,value.costs)
                or (receipt.source_status,receipt.disposition,receipt.reason) != source_decision(value)):
            raise ValueError('source receipt differs from exact retained candidate disposition')

    def _publish_source(self, payload, claim):
        receipt = self._source_record(payload,claim)
        try:
            ref = self.store.put_bytes(payload,'m6-source-disposition',c.Visibility.PRIVATE)
            self.registry.register(ref, dependencies=(receipt.candidate,self.source_configuration))
            self._observe_source(claim,(self.source_configuration,receipt.candidate,ref),receipt.costs,2)
        except (ArtifactError, RegistryError, OSError) as exc:
            raise FactoryPublicationFailed('retain exact source receipt; publication can be replayed',claim,payload) from exc
        return self._finish_source(receipt,ref)

    def _finish_source(self, receipt, reference):
        job = self.registry.job(receipt.claim.job_id)
        if job.state == 'completed': return job.result
        evidence = c.EvidenceRecord(producer='feature_rl.pipeline.Factory',
            command=('Factory.screen_source',receipt.candidate.sha256),recorded_at=receipt.recorded_at,
            exit_status=0 if receipt.disposition==c.Disposition.SUCCESS else 1, artifacts=(reference,),
            revision=self.revision,scope='source_inspection')
        result = c.OperationResult(operation='construct',disposition=receipt.disposition,artifacts=(reference,),
            evidence=(evidence,),costs=receipt.costs,reason=receipt.reason)
        snapshots = [item for item in self.registry.accounting(job.job_id).observations if item.attempt_id==receipt.claim.attempt_id]
        if len(snapshots)!=1 or snapshots[0].observation.revision!=2 or snapshots[0].observation.costs!=receipt.costs:
            raise FactoryRecoveryRequired('source accounting requires exact frozen revision',receipt.claim)
        try:
            return self.registry.complete(receipt.claim,result,observations=(snapshots[0].observation_id,)).result
        except (ArtifactError,RegistryError,OSError) as exc:
            raise FactoryRecoveryRequired('source completion unconfirmed; recover selected frozen receipt',receipt.claim) from exc

    def recover(self, claim: Claim) -> c.OperationResult:
        claim = checked(Claim,claim)
        job = self.registry.job(claim.job_id)
        if job.spec.invocation.startswith('m6-author:'):
            from .authoring import recover
            return recover(self,claim)
        if job.spec.invocation=='m6-construct':
            from .construction import recover
            return recover(self,claim)
        if job.spec.configuration!=self.source_configuration or not any(a.claim==claim for a in self.registry.attempts(job.job_id)):
            raise ValueError('claim is not this Factory source configuration')
        if job.state=='completed': return job.result
        snapshots = [x for x in self.registry.accounting(job.job_id).observations if x.attempt_id==claim.attempt_id]
        if len(snapshots)!=1 or snapshots[0].observation.revision!=2:
            raise FactoryRecoveryRequired('source outcome was not frozen; unknown attempt remains explicit',claim)
        refs = [r for r in snapshots[0].observation.receipts if r.kind=='m6-source-disposition']
        if len(refs)!=1: raise ValueError('source outcome lacks exact frozen receipt')
        payload = self.store.get_bytes(refs[0],max_envelope_bytes=4*((MAX_DOCUMENT+2)//3)+4096,max_payload_bytes=MAX_DOCUMENT)
        receipt = self._source_record(payload,claim)
        return self._finish_source(receipt,refs[0])

    def retry_publication(self, pending: FactoryPublicationFailed) -> c.OperationResult:
        from .authoring import AuthoringPending, retry
        if type(pending) is AuthoringPending:return retry(self,pending)
        if type(pending) is FactoryUpstreamPending:
            from .construction import retry_build
            return retry_build(self,pending)
        if (type(pending) is not FactoryPublicationFailed or type(pending.payload) is not bytes
                or hashlib.sha256(pending.payload).hexdigest()!=pending.sha256):
            raise ValueError('invalid retained Factory source publication')
        if pending.kind=='m6-construction-result':
            from .construction import publish
            return publish(self,pending.payload,pending.claim)
        if pending.kind=='m6-authoring-receipt':
            from .authoring import publish
            return publish(self,pending.payload,pending.claim)
        if pending.kind!='m6-source-disposition': raise ValueError('unknown Factory publication kind')
        return self._publish_source(pending.payload,pending.claim)
