"""Exact M5 admission plus selected Registry transitions; no state-string gate."""
from datetime import datetime, timezone
import hashlib
import time
import uuid
from typing import Annotated, Literal

from pydantic import Field, model_validator
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.qualification import QualificationService, QualificationRejected
from feature_rl.registry import Registry, RegistryError, QuarantinedError, Claim, CostObservation, JobSpec
from .packaging import MAX_DOCUMENT, document, checked, read_record, typed


class AdmissionRejected(ValueError):
    """No current admission is granted; historical Registry results remain readable."""


class LifecycleRecoveryRequired(Exception):
    def __init__(self, message, claim):
        super().__init__(message)
        self.claim = claim


class LifecyclePublicationFailed(LifecycleRecoveryRequired):
    def __init__(self, message, claim, payload):
        super().__init__(message, claim)
        self.payload = payload
        self.sha256 = hashlib.sha256(payload).hexdigest()


class TransitionReceipt(c.StrictModel):
    version: Literal['m6-transition-v1'] = 'm6-transition-v1'
    claim: Claim
    predecessor: c.ArtifactRef
    qualification: c.ArtifactRef | None
    built: c.ArtifactRef | None
    destination: Literal['qualified', 'released']
    target: c.TaskBundle | None
    disposition: c.Disposition
    reason: Annotated[str, Field(min_length=1, max_length=2048)]
    revision: c.Revision
    recorded_at: c.UTCDateTime
    costs: Annotated[tuple[c.CostRecord, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode='after')
    def outcome(self):
        if (self.target is not None) != (self.disposition == c.Disposition.SUCCESS):
            raise ValueError('only successful transition has a target manifest')
        if self.target is not None and (self.built is None or self.qualification is None):
            raise ValueError('successful transition requires exact BUILT/Q references')
        return self


def transition_costs(elapsed=None):
    nulls = dict(cpu_seconds=None, gpu_seconds=None, input_tokens=None,
        output_tokens=None, human_minutes=None, usd=None)
    return (
        c.CostRecord(category='construction', wall_seconds=elapsed, **nulls,
            measurement='unknown' if elapsed is None else 'partial',
            note='Lifecycle validation wall through freeze; no source/model execution. Later publication/revalidation is unmeasured.'),
        c.CostRecord(category='storage', wall_seconds=None, **nulls, measurement='unknown',
            note='Transition publication, Registry commits, current-trust rechecks and recovery overhead unmeasured, not zero'),
    )


def unchanged(built, later):
    if canonical_json(built.model_dump(mode='json', exclude={'state', 'qualification'})) != canonical_json(
            later.model_dump(mode='json', exclude={'state', 'qualification'})):
        raise AdmissionRejected('later manifest changed the complete frozen BUILT payload')


class TaskLifecycle:
    def __init__(self, *, store: ArtifactStore, registry: Registry,
                 qualification: QualificationService, revision: str):
        if (not isinstance(store, ArtifactStore) or store.role != c.ActorRole.CONTROLLER
                or not isinstance(registry, Registry) or registry.store is not store
                or type(qualification) is not QualificationService or qualification.store is not store
                or qualification.registry is not registry):
            raise TypeError('lifecycle requires the same actual M0 store, Registry and M5 QualificationService')
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact lifecycle implementation revision required')
        self.store, self.registry, self.qualification, self.revision = store, registry, qualification, revision
        self.configuration_bytes = canonical_json({'version':'m6-lifecycle-policy-v1', 'revision':revision,
            'qualification_revision':qualification.revision, 'qualification_configuration':document(qualification.configuration),
            'qualification_policy':document(qualification.policy_ref), 'sequence':['built','qualified','released']})
        self.configuration = store.put_bytes(self.configuration_bytes, 'm6-lifecycle-policy', c.Visibility.PRIVATE)
        # These are opaque bytes: the Registry must receive their exact inner refs.
        registry.register(self.configuration, dependencies=(qualification.configuration, qualification.policy_ref))

    def _spec(self, predecessor, qualification, destination):
        inputs = (predecessor,) if qualification is None else (predecessor, qualification)
        return JobSpec(operation='qualify' if destination == 'qualified' else 'release', inputs=inputs,
            configuration=self.configuration, implementation=self.revision,
            invocation='m6-transition-'+destination, attempt_limit=3)

    def _job_id(self, spec):
        return hashlib.sha256(canonical_json(document(spec))).hexdigest()

    def _task(self, reference):
        self.registry.assert_usable(reference)
        return typed(self.store, reference, c.TaskBundle)

    def _built(self, task_ref, report_ref):
        # M5 authenticates selected execution, all frozen gates and full T0
        # equality.
        accepted = self.qualification.verify_accepted(task_ref, report_ref)
        built = self._task(accepted.task)
        if built.state != c.TaskState.BUILT or built.qualification is not None:
            raise AdmissionRejected('accepted Q must resolve to unqualified BUILT T0')
        return accepted.task, built

    def _target(self, built, report_ref, destination):
        data = document(built) | {'state':destination, 'qualification':document(report_ref)}
        target = c.TaskBundle.model_validate_json(canonical_json(data))
        envelope = {'kind':target.kind,'schema_version':target.schema_version,
            'visibility':target.visibility.value,'encoding':'json','payload':document(target)}
        if len(canonical_json(envelope)) > MAX_DOCUMENT:
            raise AdmissionRejected('transition target exceeds its reader byte limit')
        return target

    def _evidence(self, receipt, reference):
        return c.EvidenceRecord(producer='feature_rl.pipeline.TaskLifecycle',
            command=('TaskLifecycle.transition', receipt.destination, receipt.predecessor.sha256),
            recorded_at=receipt.recorded_at, exit_status=0 if receipt.target is not None else 1,
            artifacts=(reference,), revision=receipt.revision, scope='source_inspection')

    def _result(self, receipt, reference, target_ref=None):
        return c.OperationResult(operation='qualify' if receipt.destination == 'qualified' else 'release',
            disposition=receipt.disposition, artifacts=(reference,) if target_ref is None else (target_ref,reference),
            evidence=(self._evidence(receipt,reference),), costs=receipt.costs, reason=receipt.reason)

    def _selected(self, predecessor, report, destination, built_ref, built):
        spec = self._spec(predecessor,report,destination)
        job = self.registry.job(self._job_id(spec))
        if job.spec != spec or job.state != 'completed' or job.result is None or job.result.disposition != c.Disposition.SUCCESS or len(job.result.artifacts) != 2:
            raise AdmissionRejected('missing exact selected successful lifecycle transition')
        target_ref, receipt_ref = job.result.artifacts
        receipt = read_record(self.store,receipt_ref,TransitionReceipt,'m6-transition')
        target = self._task(target_ref)
        expected = self._target(built,report,destination)
        if ((receipt.claim.job_id,receipt.predecessor,receipt.qualification,receipt.built,receipt.destination,receipt.revision)
                != (job.job_id,predecessor,report,built_ref,destination,self.revision)
                or receipt.target != expected or target != expected
                or job.result != self._result(receipt,receipt_ref,target_ref)):
            raise AdmissionRejected('selected lifecycle receipt, result or target differs from its exact gate')
        if not any(a.claim == receipt.claim and a.state == 'completed' for a in self.registry.attempts(job.job_id)):
            raise AdmissionRejected('transition receipt is not the selected completed attempt')
        for reference in (predecessor,report,built_ref,receipt_ref,target_ref,self.configuration):
            self.registry.assert_usable(reference)
        return target_ref, target

    def resolve_released(self, task_ref: c.ArtifactRef) -> c.TaskBundle:
        """Read current admission; never enqueue, transition, grade or execute source."""
        try:
            task_ref = checked(c.ArtifactRef,task_ref)
            task = self._task(task_ref)
            if task.state != c.TaskState.RELEASED or task.qualification is None:
                raise AdmissionRejected('exact released manifest with accepted Q is required')
            report = task.qualification
            built_ref,built = self._built(task_ref,report)
            unchanged(built,task)
            qualified,_ = self._selected(built_ref,report,'qualified',built_ref,built)
            released,actual = self._selected(qualified,report,'released',built_ref,built)
            if released != task_ref:
                raise AdmissionRejected('released artifact is not selected by its exact lifecycle chain')
            return actual
        except (ArtifactError,OSError,RegistryError,QualificationRejected,ValueError) as exc:
            raise AdmissionRejected(str(exc)) from exc

    def qualify(self, built_task: c.ArtifactRef, accepted_report: c.ArtifactRef) -> c.OperationResult:
        return self._transition(checked(c.ArtifactRef,built_task), checked(c.ArtifactRef,accepted_report), 'qualified')

    def release(self, qualified_task: c.ArtifactRef) -> c.OperationResult:
        task = typed(self.store,qualified_task,c.TaskBundle)
        return self._transition(checked(c.ArtifactRef,qualified_task), task.qualification, 'released')

    def _validate(self, predecessor, report, destination):
        prior = self._task(predecessor)
        if report is None:
            raise AdmissionRejected('release requires the selected QUALIFIED predecessor and accepted Q')
        if destination == 'qualified' and (prior.state != c.TaskState.BUILT or prior.qualification is not None):
            raise AdmissionRejected('qualification transition requires original BUILT T0')
        if destination == 'released' and (prior.state != c.TaskState.QUALIFIED or prior.qualification != report):
            raise AdmissionRejected('release requires exact QUALIFIED predecessor')
        built_ref,built = self._built(predecessor,report)
        unchanged(built,prior)
        if destination == 'qualified' and built_ref != predecessor:
            raise AdmissionRejected('accepted Q.task differs from transition predecessor')
        if destination == 'released':
            selected,_ = self._selected(built_ref,report,'qualified',built_ref,built)
            if selected != predecessor:
                raise AdmissionRejected('qualified predecessor lacks exact selected transition')
        return built_ref,self._target(built,report,destination)

    def _observe(self, claim, receipts, values, revision):
        return self.registry.reconcile(claim,CostObservation(source='m6-transition',upstream_attempt_id=claim.attempt_id,
            revision=revision,receipts=receipts,costs=values))

    def _transition(self, predecessor, report, destination):
        spec = self._spec(predecessor,report,destination)
        job = self.registry.enqueue(spec)
        if job.state == 'completed':
            if job.result.disposition == c.Disposition.SUCCESS:self._validate(predecessor,report,destination)
            return job.result
        if job.state != 'queued':
            raise LifecycleRecoveryRequired('existing transition requires explicit recovery',self.registry.attempts(job.job_id)[-1].claim)
        claim = self.registry.claim(job.job_id,owner='feature_rl.pipeline.TaskLifecycle',claim_key=uuid.uuid4().hex)
        try:self._observe(claim,(self.configuration,),transition_costs(),1)
        except (ArtifactError,OSError,RegistryError) as exc:
            raise LifecycleRecoveryRequired('transition intent is unconfirmed; do not redispatch',claim) from exc
        started = time.monotonic()
        built_ref=target=None
        try:
            built_ref,target = self._validate(predecessor,report,destination)
            disposition,reason = c.Disposition.SUCCESS,'Exact accepted T0 payload selected as '+destination+'; solver bytes unchanged'
        except (ArtifactError,OSError,RegistryError,QualificationRejected,ValueError) as exc:
            if isinstance(exc,QualificationRejected):
                disposition = c.Disposition.PROVISIONAL if exc.code == 'provisional' else c.Disposition.INVALID
            elif isinstance(exc,QuarantinedError):disposition=c.Disposition.BLOCKED
            elif isinstance(exc,RegistryError):disposition=c.Disposition.BLOCKED
            elif isinstance(exc,OSError):disposition=c.Disposition.INFRASTRUCTURE
            else:disposition=c.Disposition.INVALID
            reason = (type(exc).__name__+': '+str(exc))[:2048]
        receipt = TransitionReceipt(claim=claim,predecessor=predecessor,qualification=report,built=built_ref,
            destination=destination,target=target,disposition=disposition,reason=reason,revision=self.revision,
            recorded_at=datetime.now(timezone.utc),costs=transition_costs(time.monotonic()-started))
        payload = canonical_json(document(receipt))
        if len(payload) > MAX_DOCUMENT:
            receipt=receipt.model_copy(update={'target':None,'disposition':c.Disposition.UNSUPPORTED,
                'reason':'transition receipt exceeds its reader byte limit'})
            payload=canonical_json(document(receipt))
        return self._publish(payload,claim)

    def _publish(self, payload, claim):
        if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:
            raise AdmissionRejected('invalid bounded transition publication')
        receipt = TransitionReceipt.model_validate_json(payload)
        if canonical_json(document(receipt))!=payload or receipt.claim!=claim:
            raise AdmissionRejected('invalid bounded transition publication')
        job=self.registry.job(claim.job_id)
        if (job.spec!=self._spec(receipt.predecessor,receipt.qualification,receipt.destination)
                or receipt.revision!=self.revision
                or not any(a.claim==claim for a in self.registry.attempts(claim.job_id))):
            raise AdmissionRejected('pending transition does not match its original claim/configuration')
        dependencies=tuple(dict.fromkeys((self.configuration,receipt.predecessor,
            *(() if receipt.qualification is None else (receipt.qualification,)),
            *(() if receipt.built is None else (receipt.built,)))))
        try:
            reference=self.store.put_bytes(payload,'m6-transition',c.Visibility.PRIVATE)
            self.registry.register(reference,dependencies=dependencies)
            self._observe(claim,(self.configuration,reference),receipt.costs,2)
        except (ArtifactError,OSError,RegistryError) as exc:
            raise LifecyclePublicationFailed('retain exact transition receipt without repeating its attempt',claim,payload) from exc
        return self._finish(receipt,reference)

    def _finish(self, receipt, reference):
        claim=receipt.claim;spec=self._spec(receipt.predecessor,receipt.qualification,receipt.destination)
        job=self.registry.job(claim.job_id)
        if job.spec!=spec or receipt.revision!=self.revision:
            raise AdmissionRejected('transition receipt belongs to a different service configuration')
        if job.state=='completed':return job.result
        if not any(a.claim==claim and a.state=='running' for a in self.registry.attempts(job.job_id)):
            raise AdmissionRejected('transition receipt lacks current running claim')
        try:
            target_ref=None
            if receipt.target is not None:
                built,target=self._validate(receipt.predecessor,receipt.qualification,receipt.destination)
                if built!=receipt.built or target!=receipt.target:
                    raise AdmissionRejected('frozen transition changed after gate validation')
                target_ref=self.store.put_artifact(target)
                self.registry.register(target_ref,dependencies=(reference,))
            snapshots=tuple(item for item in self.registry.accounting(claim.job_id).observations if item.attempt_id==claim.attempt_id)
            if len(snapshots)!=1 or snapshots[0].observation.revision!=2 or snapshots[0].observation.costs!=receipt.costs:
                raise AdmissionRejected('transition lacks exact frozen accounting snapshot')
            return self.registry.complete(claim,self._result(receipt,reference,target_ref),
                observations=(snapshots[0].observation_id,)).result
        except (ArtifactError,OSError,RegistryError,QualificationRejected,ValueError) as exc:
            raise LifecycleRecoveryRequired('frozen transition not selected; recover exact receipt and recheck current admission',claim) from exc

    def recover(self, claim: Claim) -> c.OperationResult:
        claim=checked(Claim,claim);job=self.registry.job(claim.job_id)
        if not any(a.claim==claim for a in self.registry.attempts(job.job_id)):
            raise AdmissionRejected('unknown transition claim')
        if job.spec.configuration!=self.configuration or job.spec.implementation!=self.revision:
            raise AdmissionRejected('claim is not this lifecycle configuration')
        if job.state=='completed':return job.result
        snapshots=[item for item in self.registry.accounting(claim.job_id).observations if item.attempt_id==claim.attempt_id]
        if len(snapshots)!=1 or snapshots[0].observation.revision!=2:
            raise LifecycleRecoveryRequired('transition was not durably frozen; unknown attempt must be reconciled',claim)
        refs=[ref for ref in snapshots[0].observation.receipts if ref.kind=='m6-transition']
        if len(refs)!=1:raise AdmissionRejected('missing exact frozen transition receipt')
        receipt=read_record(self.store,refs[0],TransitionReceipt,'m6-transition')
        if receipt.claim!=claim:raise AdmissionRejected('transition receipt claim mismatch')
        return self._finish(receipt,refs[0])

    def retry_publication(self, pending: LifecyclePublicationFailed) -> c.OperationResult:
        if not isinstance(pending,LifecyclePublicationFailed) or type(pending.payload) is not bytes or hashlib.sha256(pending.payload).hexdigest()!=pending.sha256:
            raise AdmissionRejected('exact pending transition publication required')
        return self._publish(pending.payload,pending.claim)
