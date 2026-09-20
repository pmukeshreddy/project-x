"""Durable Registry-backed patch and rejected-source human audits."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import uuid
from typing import Mapping

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, ArtifactStore, canonical_json
from feature_rl.grading import read_grade
from feature_rl.pipeline import read_source_disposition
from feature_rl.pipeline.factory import source_decision
from feature_rl.audits.attestation import NAMESPACE, SSHHumanVerifier, verify_sshsig
from feature_rl.registry import Claim, CostObservation, JobSpec, Registry, RegistryError
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_bytes, read_local

from .models import (
    AdjudicatedPatchSample,
    AdjudicatedSourceSample,
    AuditExecutionReport,
    AuditPopulationFrame,
    AuditSamplingPlan,
    AuditSelectionManifest,
    DetachedAuditAttestation,
    PatchAuditAdjudication,
    PatchAuditOutcome,
    PatchAuditSelection,
    PatchFrameEntry,
    QuarantineAction,
    SourceAuditAdjudication,
    SourceAuditOutcome,
    SourceAuditSelection,
    SourceFrameEntry,
)
from .selection import validate_selection_manifest
from .statistics import summarize_audits, summarize_source_audits


class AuditRejected(ValueError):
    """The frame, receipt, selection, or human attestation is not exact."""


class AuditRecoveryRequired(Exception):
    """A selected audit attempt must be recovered without redoing human work."""

    def __init__(self, message: str, claim: Claim):
        super().__init__(message)
        self.claim = claim


class AuditPublicationFailed(AuditRecoveryRequired):
    """Exact frozen report bytes can be published again without re-auditing."""

    def __init__(self, message: str, claim: Claim, *, payload: bytes, dependencies: tuple[c.ArtifactRef, ...], kind: str):
        super().__init__(message, claim)
        self.payload = payload
        self.dependencies = dependencies
        self.kind = kind
        self.sha256 = hashlib.sha256(payload).hexdigest()


def _unknown_audit_cost() -> c.CostRecord:
    return c.CostRecord(
        category="human_review", wall_seconds=None, cpu_seconds=None,
        gpu_seconds=None, input_tokens=None, output_tokens=None,
        human_minutes=None, usd=None, measurement="unknown",
        note="human and controller audit cost was not measured",
    )


def _refs(evidence: tuple[c.EvidenceRecord, ...]) -> tuple[c.ArtifactRef, ...]:
    return tuple(dict.fromkeys(ref for item in evidence for ref in item.artifacts))


class AuditService:
    """Audit an entire frozen selection; historical reads grant no admission."""

    def __init__(
        self, *, store: ArtifactStore, registry: Registry,
        human_verifier: SSHHumanVerifier, selection_manifest: c.ArtifactRef,
        attestations: Mapping[str, c.ArtifactRef], revision: str,
    ):
        if not isinstance(store, ArtifactStore) or not isinstance(registry, Registry) or registry.store is not store:
            raise TypeError("audit requires the controller store used by the real Registry")
        if not isinstance(human_verifier, SSHHumanVerifier):
            raise TypeError("audit requires the M5 external SSH human verifier")
        if type(attestations) is not dict or any(type(key) is not str or not isinstance(value, c.ArtifactRef) for key, value in attestations.items()):
            raise TypeError("attestations must be an exact subject-to-reference dict")
        if type(revision) is not str or len(revision) not in (40, 64) or any(char not in "0123456789abcdef" for char in revision):
            raise ValueError("implementation revision required")
        self.store, self.registry = store, registry
        self.human_verifier, self.revision = human_verifier, revision
        self.selection_ref = c.ArtifactRef.model_validate(selection_manifest)
        self.selection = read_local(store, self.selection_ref, AuditSelectionManifest, "m8-audit-selection")
        self.population_ref = self.selection.population_frame
        self.plan_ref = self.selection.sampling_plan
        self.population = read_local(store, self.population_ref, AuditPopulationFrame, "m8-audit-population")
        self.plan = read_local(store, self.plan_ref, AuditSamplingPlan, "m8-audit-sampling")
        validate_selection_manifest(self.population_ref, self.population, self.plan_ref, self.plan, self.selection)
        selected_ids = {item.subject_id for item in self.selection.selections}
        if set(attestations) - selected_ids:
            raise AuditRejected("attestation supplied for a subject outside the frozen selection")
        self.attestations = dict(attestations)

        frame_dependencies = tuple(
            ref for item in self.population.entries if item.unit == "source"
            for ref in (item.candidate, item.source_disposition)
        )
        registry.register(self.population_ref, dependencies=frame_dependencies)
        registry.register(self.plan_ref, dependencies=(self.population_ref,))
        registry.register(self.selection_ref, dependencies=(self.population_ref, self.plan_ref))
        attestation_parts = []
        for reference in self.attestations.values():
            envelope = read_local(store, reference, DetachedAuditAttestation, "m8-detached-audit-attestation")
            registry.register(reference, dependencies=(envelope.payload, envelope.signature))
            attestation_parts.extend((reference, envelope.payload, envelope.signature))
        config_bytes = canonical_json({
            "version": "m8-audit-configuration-v2",
            "selection": self.selection_ref.model_dump(mode="json"),
            "attestations": {
                key: value.model_dump(mode="json") for key, value in sorted(self.attestations.items())
            },
            "revision": revision,
        })
        self.configuration = store.put_bytes(config_bytes, "m8-audit-configuration", c.Visibility.PRIVATE)
        registry.register(
            self.configuration,
            dependencies=(self.selection_ref, *tuple(value for _, value in sorted(self.attestations.items()))),
        )
        self.audit_protected = tuple(dict.fromkeys((
            self.population_ref, self.plan_ref, self.selection_ref,
            self.configuration, *attestation_parts,
        )))
        self.audit_configuration = self.configuration

    def _rollout_and_grade(self, run_id: str):
        try:
            job = self.registry.job(run_id)
        except Exception as exc:
            raise AuditRejected(f"unknown audit run {run_id}") from exc
        if job.job_id != run_id or job.state != "completed" or job.result is None or job.result.operation != "run":
            raise AuditRejected(f"audit run {run_id} is incomplete or is not a completed run operation")
        rollout_refs = tuple(ref for ref in job.result.artifacts if ref.kind == "RolloutRecord")
        if len(rollout_refs) != 1:
            raise AuditRejected(f"audit run {run_id} must contain exactly one RolloutRecord")
        rollout_ref = rollout_refs[0]
        rollout = self.store.get_artifact(rollout_ref)
        if type(rollout) is not c.RolloutRecord or rollout.run_id != run_id:
            raise AuditRejected(f"audit run {run_id} RolloutRecord identity mismatch")
        grade_refs = tuple(
            ref for evidence in rollout.grading_evidence for ref in evidence.artifacts
            if ref.kind == "m4-grade-receipt"
        )
        if len(grade_refs) != 1:
            raise AuditRejected(f"audit run {run_id} must bind exactly one M4 grade receipt")
        grade_ref = grade_refs[0]
        grade = read_grade(self.store, grade_ref)
        if (
            rollout.submission is None or grade.task != rollout.task
            or grade.submission != rollout.submission
            or rollout.seeds.seeds != (grade.case_seed,)
            or grade.disposition != rollout.disposition or grade.reward != rollout.reward
            or grade.verifier is None
        ):
            raise AuditRejected(f"audit run {run_id} rollout and grade bindings differ")
        task = self.store.get_artifact(rollout.task)
        if type(task) is not c.TaskBundle:
            raise AuditRejected("audited rollout task is not a TaskBundle")
        return rollout_ref, rollout, grade_ref, grade, task

    @staticmethod
    def _verifier_decision(disposition: c.Disposition) -> str:
        if disposition == c.Disposition.SUCCESS:
            return "accepted"
        if disposition == c.Disposition.REJECTED:
            return "rejected"
        if disposition == c.Disposition.INFRASTRUCTURE:
            return "invalid_environment"
        return "invalid_measurement"

    def _patch_subject(self, entry: PatchFrameEntry):
        subject = self._rollout_and_grade(entry.run_id)
        _, _, _, grade, task = subject
        expected_failure = "none" if grade.disposition == c.Disposition.SUCCESS else grade.disposition.value
        if (
            entry.repository_family != task.repository_family
            or entry.verifier_decision != self._verifier_decision(grade.disposition)
            or entry.failure_category != expected_failure
        ):
            raise AuditRejected("patch frame family/status/failure category differs from actual run")
        return subject

    def _source_subject(self, entry: SourceFrameEntry):
        try:
            job = self.registry.job(entry.construct_job_id)
        except Exception as exc:
            raise AuditRejected(f"unknown rejected-source construct job {entry.construct_job_id}") from exc
        if (
            job.job_id != entry.construct_job_id or job.state != "completed"
            or job.result is None or job.result.operation != "construct"
            or job.result.disposition != c.Disposition.REJECTED
        ):
            raise AuditRejected("rejected-source unit requires an actual completed rejected construct result")
        candidate_inputs = tuple(ref for ref in job.spec.inputs if ref.kind == "CandidateRecord")
        if (
            candidate_inputs != (entry.candidate,)
            or job.spec.invocation != "m6-source-admission"
            or job.result.artifacts != (entry.source_disposition,)
        ):
            raise AuditRejected("construct job does not consume the exact selected CandidateRecord")
        candidate = self.store.get_artifact(entry.candidate)
        receipt = read_source_disposition(self.store, entry.source_disposition)
        if (
            type(candidate) is not c.CandidateRecord
            or candidate.repository_family != entry.repository_family
            or entry.failure_category != job.result.disposition.value
            or receipt.claim.job_id != job.job_id
            or receipt.candidate != entry.candidate
            or receipt.repository_family != candidate.repository_family
            or receipt.request_lineage != candidate.request_lineage
            or receipt.screening != candidate.screening
            or receipt.license != candidate.license
            or receipt.partition != candidate.partition
            or receipt.original_costs != candidate.costs
            or receipt.source_status != "rejected"
            or receipt.disposition != c.Disposition.REJECTED
            or (receipt.source_status, receipt.disposition, receipt.reason) != source_decision(candidate)
        ):
            raise AuditRejected("rejected-source frame differs from actual candidate/result disposition")
        evidence = tuple(dict.fromkeys((entry.source_disposition, *_refs(candidate.screening.evidence), *_refs(job.result.evidence))))
        if not evidence:
            raise AuditRejected("rejected-source result has no retained source evidence")
        return job, candidate, evidence

    def _population_subjects(self):
        subjects = {}
        for entry in self.population.entries:
            subjects[entry.subject_id] = (
                self._patch_subject(entry) if entry.unit == "patch"
                else self._source_subject(entry)
            )
        return subjects

    def _notice_id(self, root, evidence):
        finding = canonical_json({
            "version": "m8-audit-finding-v1",
            "configuration": self.configuration.model_dump(mode="json"),
            "root": root.model_dump(mode="json"),
            "evidence": [ref.model_dump(mode="json") for ref in evidence],
        })
        return "m8-audit-" + hashlib.sha256(finding).hexdigest()[:32]

    def _verify_human(self, selection, attestation_ref, model, kind):
        envelope = read_local(self.store, attestation_ref, DetachedAuditAttestation, "m8-detached-audit-attestation")
        raw = read_bytes(self.store, envelope.payload, 65536, kind, True)
        try:
            payload = model.model_validate_json(canonical_json(decode_json(raw, 65536)))
        except ValueError as exc:
            raise AuditRejected("invalid audit adjudication payload") from exc
        if raw != canonical_json(payload.model_dump(mode="json")) or payload.sample != selection or payload.human_identity != envelope.principal:
            raise AuditRejected("audit adjudication canonical sample/reviewer binding mismatch")
        read_bytes(self.store, payload.counterexample, 4 * 1024 * 1024, private=True)
        now = datetime.now(timezone.utc)
        enrollment = self.human_verifier.enrollment(now)
        matches = [reviewer for reviewer in enrollment.reviewers if reviewer.human_identity == envelope.principal]
        if len(matches) != 1 or matches[0].fingerprint in enrollment.revoked_fingerprints:
            raise AuditRejected("audit signer is absent or revoked in external enrollment")
        reviewer = matches[0]
        if not enrollment.valid_after <= payload.reviewed_at <= enrollment.valid_before:
            raise AuditRejected("audit adjudication time is outside the enrollment interval")
        allowed = (reviewer.human_identity + ' namespaces="' + NAMESPACE + '" ' + reviewer.public_key + "\n").encode()
        signature = read_bytes(self.store, envelope.signature, 16384, "m8-sshsig", True)
        verification = verify_sshsig(
            raw, signature, allowed, reviewer.human_identity, NAMESPACE,
            binary_sha256=enrollment.binary_sha256,
        )
        if self.human_verifier.enrollment(datetime.now(timezone.utc)) != enrollment:
            raise AuditRejected("external enrollment changed during audit verification")
        verification.update(
            human_origin_verified=True,
            enrollment_sha256=self.human_verifier.expected_enrollment_sha256,
            human_identity=payload.human_identity, fingerprint=reviewer.fingerprint,
            attestation=attestation_ref.model_dump(mode="json"),
        )
        verification_ref = self.store.put_bytes(
            canonical_json(verification), "m8-human-audit-verification", c.Visibility.PRIVATE,
        )
        evidence = c.EvidenceRecord(
            producer="feature_rl.audits", command=("AuditService.audit", selection.unit, selection.subject_id),
            recorded_at=datetime.now(timezone.utc), exit_status=0,
            artifacts=(attestation_ref, verification_ref, payload.counterexample),
            revision=self.revision, scope="human_review",
        )
        return payload, evidence, verification_ref

    def _execute(self, subjects=None):
        subjects = self._population_subjects() if subjects is None else subjects
        patch_outcomes, source_outcomes = [], []
        patch_samples, source_samples = [], []
        defects, source_defects = {}, {}
        dependencies = [self.configuration, self.selection_ref, self.population_ref, self.plan_ref]
        for entry in self.population.entries:
            actual = subjects[entry.subject_id]
            if entry.unit == "patch":
                rollout_ref, rollout, grade_ref, grade, _ = actual
                dependencies.extend((rollout_ref, grade_ref, rollout.task, grade.submission, grade.verifier))
            else:
                job, _, source_evidence = actual
                dependencies.extend((entry.candidate, entry.source_disposition, *job.result.artifacts, *_refs(job.result.evidence), *source_evidence))
        human_evidence = []
        for selection in self.selection.selections:
            actual = subjects[selection.subject_id]
            attestation_ref = self.attestations.get(selection.subject_id)
            if selection.unit == "patch":
                _, rollout, _, grade, _ = actual
                if attestation_ref is None:
                    patch_samples.append(AdjudicatedPatchSample(
                        selection=selection, patch_validity="unresolved",
                        environment_validity="unresolved", checker_assessment="unresolved",
                        attestation_status="unavailable",
                    ))
                    patch_outcomes.append(PatchAuditOutcome(
                        selection=selection, adjudication=None, record=None,
                        issue="selected patch has no authenticated adjudication",
                    ))
                    continue
                payload, evidence, verification_ref = self._verify_human(
                    selection, attestation_ref, PatchAuditAdjudication,
                    "m8-patch-audit-adjudication",
                )
                if payload.task != rollout.task or payload.verifier != grade.verifier or payload.submission != rollout.submission:
                    raise AuditRejected("patch adjudication task/verifier/submission binding mismatch")
                review = c.HumanReview(
                    actor_type="human", human_identity=payload.human_identity,
                    subject_sha256=payload.submission.sha256,
                    decision=("approved" if payload.patch_validity == "valid" else "rejected" if payload.patch_validity == "invalid" else "unresolved"),
                    evidence=(evidence,), attestation=attestation_ref,
                )
                record = c.AuditRecord(
                    submission=payload.submission, task=payload.task, verifier=payload.verifier,
                    sampling_probability=selection.sampling_probability,
                    sampling_kind=selection.sampling_kind, verdict=payload.patch_validity,
                    human_review=review, evidence=(evidence,),
                )
                patch_samples.append(AdjudicatedPatchSample(
                    selection=selection, patch_validity=payload.patch_validity,
                    environment_validity=payload.environment_validity,
                    checker_assessment=payload.checker_assessment,
                    attestation_status="authenticated",
                ))
                patch_outcomes.append(PatchAuditOutcome(
                    selection=selection, adjudication=payload, record=record,
                    issue="authenticated patch adjudication",
                ))
                dependencies.extend((attestation_ref, verification_ref, payload.counterexample))
                human_evidence.append(evidence)
                if payload.checker_assessment == "defect":
                    defects.setdefault(payload.verifier, []).append((payload.counterexample, verification_ref))
            else:
                _, candidate, source_evidence = actual
                if attestation_ref is None:
                    source_samples.append(AdjudicatedSourceSample(
                        selection=selection, source_validity="unresolved",
                        source_assessment="unresolved", attestation_status="unavailable",
                    ))
                    source_outcomes.append(SourceAuditOutcome(
                        selection=selection, adjudication=None, evidence=(),
                        issue="selected rejected source has no authenticated adjudication",
                    ))
                    continue
                payload, evidence, verification_ref = self._verify_human(
                    selection, attestation_ref, SourceAuditAdjudication,
                    "m8-source-audit-adjudication",
                )
                if payload.candidate != selection.candidate or payload.source_evidence != source_evidence:
                    raise AuditRejected("source adjudication candidate/evidence binding mismatch")
                source_samples.append(AdjudicatedSourceSample(
                    selection=selection, source_validity=payload.source_validity,
                    source_assessment=payload.source_assessment,
                    attestation_status="authenticated",
                ))
                source_outcomes.append(SourceAuditOutcome(
                    selection=selection, adjudication=payload, evidence=(evidence,),
                    issue="authenticated rejected-source adjudication",
                ))
                dependencies.extend((attestation_ref, verification_ref, payload.counterexample, candidate.provenance.inputs[0] if candidate.provenance.inputs else selection.candidate))
                human_evidence.append(evidence)
                if payload.source_assessment == "defect":
                    source_defects.setdefault(selection.candidate, []).append((payload.counterexample, verification_ref))

        quarantined, quarantined_sources, quarantine_actions = [], [], []
        affected_runs, affected_checkpoints = set(), set()
        for root, findings, target in (
            *((root, findings, quarantined) for root, findings in defects.items()),
            *((root, findings, quarantined_sources) for root, findings in source_defects.items()),
        ):
            evidence_refs = tuple(dict.fromkeys(ref for finding in findings for ref in finding))
            trace = self.registry.trace(root)
            target.append(root)
            affected_runs.update(trace.runs)
            affected_checkpoints.update(trace.checkpoints)
            quarantine_actions.append(QuarantineAction(
                root=root, notice_id=self._notice_id(root, evidence_refs),
                reason="authenticated M8 human audit found a decision defect",
                evidence=evidence_refs,
            ))
        actions = () if not defects and not source_defects else (
            "regrade saved submissions after checker defects with an unaffected verifier",
            "restart training from an unaffected checkpoint or disclose contamination after weight updates",
            "retain and correct rejected-source screening without relabeling patch audit denominators",
        )
        report = AuditExecutionReport(
            version="m8-audit-report-v2", population_frame=self.population_ref,
            sampling_plan=self.plan_ref, selection_manifest=self.selection_ref,
            patch_outcomes=tuple(patch_outcomes), source_outcomes=tuple(source_outcomes),
            patch_statistics=summarize_audits(tuple(patch_samples)),
            source_statistics=summarize_source_audits(tuple(source_samples)),
            quarantined_verifiers=tuple(sorted(quarantined, key=lambda ref: ref.sha256)),
            quarantined_sources=tuple(sorted(quarantined_sources, key=lambda ref: ref.sha256)),
            affected_runs=tuple(sorted(affected_runs, key=lambda ref: ref.sha256)),
            affected_checkpoints=tuple(sorted(affected_checkpoints, key=lambda ref: ref.sha256)),
            quarantine_actions=tuple(sorted(quarantine_actions, key=lambda action: action.root.sha256)),
            required_action=actions,
        )
        complete = all(item.attestation_status == "authenticated" for item in (*patch_samples, *source_samples))
        return report, tuple(dict.fromkeys(dependencies)), tuple(human_evidence), complete

    def _spec(self, configuration=None):
        return JobSpec(
            operation="audit", inputs=(self.selection_ref,),
            configuration=self.audit_configuration if configuration is None else configuration,
            implementation=self.revision, invocation="m8-audit-v2", attempt_limit=1,
        )

    def _subject_roots(self, subjects):
        roots = []
        for entry in self.population.entries:
            actual = subjects[entry.subject_id]
            if entry.unit == "patch":
                _, rollout, _, grade, _ = actual
                roots.extend((rollout.task, grade.submission, grade.verifier))
            else:
                roots.extend((entry.candidate, entry.source_disposition))
        return tuple(dict.fromkeys(roots))

    def _scoped_configuration(self, subjects):
        return self.registry.historical_audit_configuration(
            self.configuration,
            subjects=self._subject_roots(subjects),
            protected=self.audit_protected,
        )

    def _validate_claim_configuration(self, claim):
        job = self.registry.job(claim.job_id)
        if not any(attempt.claim == claim for attempt in self.registry.attempts(job.job_id)):
            raise ValueError("claim is not this audit service configuration")
        subjects = self._population_subjects()
        expected = self._scoped_configuration(subjects)
        if job.spec != self._spec(expected):
            raise ValueError("claim belongs to a different audit service configuration")
        self.audit_configuration = expected
        return job

    def _result(self, reference, payload):
        if reference.kind == "m8-audit-report":
            report = AuditExecutionReport.model_validate_json(payload)
            expected_patch = tuple(
                item for item in self.selection.selections if item.unit == "patch"
            )
            expected_source = tuple(
                item for item in self.selection.selections if item.unit == "source"
            )
            if (
                (report.population_frame, report.sampling_plan, report.selection_manifest)
                != (self.population_ref, self.plan_ref, self.selection_ref)
                or tuple(item.selection for item in report.patch_outcomes) != expected_patch
                or tuple(item.selection for item in report.source_outcomes) != expected_source
                or {item.root for item in report.quarantine_actions}
                != set((*report.quarantined_verifiers, *report.quarantined_sources))
            ):
                raise ValueError("report differs from the frozen audit inputs or effects")
            complete = all(
                outcome.adjudication is not None
                for outcome in (*report.patch_outcomes, *report.source_outcomes)
            )
            human_evidence = tuple(
                evidence
                for outcome in report.patch_outcomes if outcome.record is not None
                for evidence in outcome.record.evidence
            ) + tuple(
                evidence
                for outcome in report.source_outcomes if outcome.adjudication is not None
                for evidence in outcome.evidence
            )
            evidence = c.EvidenceRecord(
                producer="feature_rl.audits", command=("AuditService.audit", *tuple(sorted(
                    item.run_id for item in self.selection.selections if item.unit == "patch"
                ))), recorded_at=self.selection.created_at, exit_status=0,
                artifacts=(reference,), revision=self.revision,
                scope="human_review" if human_evidence else "source_inspection",
            )
            return c.OperationResult(
                operation="audit",
                disposition=c.Disposition.SUCCESS if complete else c.Disposition.PROVISIONAL,
                artifacts=(reference,), evidence=(evidence, *human_evidence),
                costs=(_unknown_audit_cost(),),
                reason="complete frozen audit selection recorded; unavailable adjudications remain explicit",
            ), report
        failure = decode_json(payload, 65536)
        if (
            type(failure) is not dict
            or failure.get("version") != "m8-audit-failure-v1"
            or failure.get("configuration") != self.configuration.model_dump(mode="json")
            or type(failure.get("reason")) is not str
            or type(failure.get("recorded_at")) is not str
        ):
            raise ValueError("failure report differs from the frozen audit configuration")
        evidence = c.EvidenceRecord(
            producer="feature_rl.audits", command=("AuditService.audit",),
            recorded_at=datetime.fromisoformat(failure["recorded_at"]), exit_status=1,
            artifacts=(reference,), revision=self.revision, scope="source_inspection",
        )
        return c.OperationResult(
            operation="audit", disposition=c.Disposition.INVALID,
            artifacts=(reference,), evidence=(evidence,), costs=(_unknown_audit_cost(),),
            reason=failure["reason"],
        ), None

    def _apply_effects(self, report):
        for action in report.quarantine_actions:
            matching = [notice for notice in self.registry.trace(action.root).notices
                        if notice.notice_id == action.notice_id]
            if matching:
                notice = matching[0]
                if (notice.root, notice.reason, notice.evidence, notice.active) != (
                    action.root, action.reason, action.evidence, True,
                ):
                    raise AuditRejected("quarantine action identity conflicts with retained history")
                continue
            self.registry.quarantine(
                action.root, notice_id=action.notice_id,
                reason=action.reason, evidence=action.evidence,
            )

    def _publish(self, claim, *, payload, dependencies, kind):
        try:
            reference = self.store.put_bytes(payload, kind, c.Visibility.PRIVATE)
            self.registry.register(reference, dependencies=dependencies)
            result, report = self._result(reference, payload)
            observation = self.registry.reconcile(claim, CostObservation(
                source="m8-audit", upstream_attempt_id=claim.attempt_id,
                revision=1, receipts=(self.audit_configuration, reference),
                costs=result.costs,
            ))
        except (ArtifactError, RegistryError, OSError) as exc:
            raise AuditPublicationFailed(
                "retain exact audit bytes and dependencies; publication can be replayed",
                claim, payload=payload, dependencies=dependencies, kind=kind,
            ) from exc
        try:
            if report is not None:
                self._apply_effects(report)
            return self.registry.complete(
                claim, result, observations=(observation.observation_id,),
            ).result
        except (ArtifactError, RegistryError, OSError, AuditRejected) as exc:
            raise AuditRecoveryRequired(
                "audit report is frozen; recover its durable effects and completion", claim,
            ) from exc

    def audit(self, run_ids: tuple[str, ...]) -> c.OperationResult:
        if type(run_ids) is not tuple or any(type(item) is not str for item in run_ids):
            raise TypeError("audit run IDs must be a tuple")
        expected = tuple(sorted(
            item.run_id for item in self.selection.selections if item.unit == "patch"
        ))
        if tuple(sorted(run_ids)) != expected or len(run_ids) != len(set(run_ids)):
            raise AuditRejected("audit must account for the complete frozen patch selection")
        subjects = self._population_subjects()
        self.audit_configuration = self._scoped_configuration(subjects)
        job = self.registry.enqueue(self._spec())
        if job.state == "completed":
            return job.result
        if job.state != "queued":
            raise AuditRejected("existing audit attempt requires controller recovery")
        claim = self.registry.claim(
            job.job_id, owner="feature_rl.audits.AuditService", claim_key=uuid.uuid4().hex,
        )
        try:
            report, dependencies, human_evidence, complete = self._execute(subjects)
            payload = canonical_json(report.model_dump(mode="json"))
            kind = "m8-audit-report"
        except AuditRejected as exc:
            payload = canonical_json({
                "version": "m8-audit-failure-v1", "configuration": self.configuration.model_dump(mode="json"),
                "reason": str(exc), "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
            dependencies = (self.configuration, self.selection_ref)
            kind = "m8-audit-failure"
        return self._publish(claim, payload=payload, dependencies=dependencies, kind=kind)

    def recover(self, claim: Claim) -> c.OperationResult:
        claim = Claim.model_validate(claim)
        job = self._validate_claim_configuration(claim)
        if job.state == "completed":
            return job.result
        observations = [item for item in self.registry.accounting(job.job_id).observations
                        if item.attempt_id == claim.attempt_id and item.observation.revision == 1]
        if len(observations) != 1:
            raise AuditRecoveryRequired("audit outcome was not frozen; retained claim requires reconciliation", claim)
        refs = tuple(ref for ref in observations[0].observation.receipts
                     if ref.kind in {"m8-audit-report", "m8-audit-failure"})
        if len(refs) != 1:
            raise AuditRecoveryRequired("audit accounting lacks one frozen outcome", claim)
        reference = refs[0]
        payload = self.store.get_bytes(reference, max_envelope_bytes=8 * 1024 * 1024,
                                       max_payload_bytes=4 * 1024 * 1024)
        result, report = self._result(reference, payload)
        if report is not None:
            self._apply_effects(report)
        return self.registry.complete(
            claim, result, observations=(observations[0].observation_id,),
        ).result

    def retry_publication(self, pending: AuditPublicationFailed) -> c.OperationResult:
        if (type(pending) is not AuditPublicationFailed
                or hashlib.sha256(pending.payload).hexdigest() != pending.sha256
                or pending.kind not in {"m8-audit-report", "m8-audit-failure"}):
            raise ValueError("invalid retained audit publication")
        self._validate_claim_configuration(pending.claim)
        if not {self.configuration, self.selection_ref}.issubset(pending.dependencies):
            raise ValueError("retained audit publication belongs to a different audit service")
        return self._publish(
            pending.claim, payload=pending.payload,
            dependencies=pending.dependencies, kind=pending.kind,
        )
