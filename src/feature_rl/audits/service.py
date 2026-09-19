"""Registry-backed, externally authenticated human audit execution."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.grading import read_grade
from feature_rl.qualification.attestation import NAMESPACE, SSHHumanVerifier, verify_sshsig
from feature_rl.registry import Registry
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_bytes, read_local

from .models import (
    AdjudicatedSample,
    AuditAdjudication,
    AuditExecutionReport,
    AuditSelection,
    AuditSelectionManifest,
    DetachedAuditAttestation,
)
from .statistics import summarize_audits


class AuditRejected(ValueError):
    """The registry, receipt, sample, or human attestation is not exact."""


def _unknown_audit_cost() -> c.CostRecord:
    return c.CostRecord(
        category="human_review", wall_seconds=None, cpu_seconds=None,
        gpu_seconds=None, input_tokens=None, output_tokens=None,
        human_minutes=None, usd=None, measurement="unknown",
        note="human and controller audit cost was not measured",
    )


class AuditService:
    """Audit frozen selected runs without granting task or policy admission."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        registry: Registry,
        human_verifier: SSHHumanVerifier,
        selection_manifest: c.ArtifactRef,
        attestations: Mapping[str, c.ArtifactRef],
        revision: str,
    ):
        if not isinstance(store, ArtifactStore) or not isinstance(registry, Registry) or registry.store is not store:
            raise TypeError("audit requires the controller store used by the real Registry")
        if not isinstance(human_verifier, SSHHumanVerifier):
            raise TypeError("audit requires the M5 external SSH human verifier")
        if type(attestations) is not dict or any(type(key) is not str or not isinstance(value, c.ArtifactRef) for key, value in attestations.items()):
            raise TypeError("attestations must be an exact run-to-reference dict")
        if type(revision) is not str or len(revision) not in (40, 64) or any(char not in "0123456789abcdef" for char in revision):
            raise ValueError("implementation revision required")
        self.store = store
        self.registry = registry
        self.human_verifier = human_verifier
        self.selection_ref = c.ArtifactRef.model_validate(selection_manifest)
        self.selection = read_local(store, self.selection_ref, AuditSelectionManifest, "m8-audit-selection")
        self.attestations = dict(attestations)
        self.revision = revision

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
            rollout.submission is None
            or grade.task != rollout.task
            or grade.submission != rollout.submission
            or rollout.seeds.seeds != (grade.case_seed,)
            or grade.disposition != rollout.disposition
            or grade.reward != rollout.reward
            or grade.verifier is None
        ):
            raise AuditRejected(f"audit run {run_id} rollout and grade bindings differ")
        return rollout_ref, rollout, grade_ref, grade

    @staticmethod
    def _verifier_decision(disposition: c.Disposition) -> str:
        if disposition == c.Disposition.SUCCESS:
            return "accepted"
        if disposition == c.Disposition.REJECTED:
            return "rejected"
        return "invalid"

    def _authenticate(self, selection: AuditSelection, attestation_ref: c.ArtifactRef, rollout, grade):
        envelope = read_local(
            self.store, attestation_ref, DetachedAuditAttestation,
            "m8-detached-audit-attestation",
        )
        raw = read_bytes(
            self.store, envelope.payload, 65536,
            "m8-audit-adjudication", True,
        )
        try:
            payload = AuditAdjudication.model_validate_json(canonical_json(decode_json(raw, 65536)))
        except ValueError as exc:
            raise AuditRejected("invalid audit adjudication payload") from exc
        if raw != canonical_json(payload.model_dump(mode="json")):
            raise AuditRejected("audit adjudication must use exact canonical signing bytes")
        if (
            payload.sample != selection
            or payload.run_id != selection.run_id
            or payload.task != rollout.task
            or payload.verifier != grade.verifier
            or payload.submission != rollout.submission
            or payload.human_identity != envelope.principal
        ):
            raise AuditRejected("audit adjudication sample/task/verifier/submission binding mismatch")
        # Existence and a bounded payload are part of a reproducible counterexample.
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
            human_identity=payload.human_identity,
            fingerprint=reviewer.fingerprint,
            attestation=attestation_ref.model_dump(mode="json"),
        )
        verification_ref = self.store.put_bytes(
            canonical_json(verification), "m8-human-audit-verification", c.Visibility.PRIVATE,
        )
        evidence = c.EvidenceRecord(
            producer="feature_rl.audits", command=("AuditService.audit", selection.run_id),
            recorded_at=datetime.now(timezone.utc), exit_status=0,
            artifacts=(attestation_ref, verification_ref, payload.counterexample),
            revision=self.revision, scope="human_review",
        )
        human_review = c.HumanReview(
            actor_type="human", human_identity=payload.human_identity,
            subject_sha256=payload.submission.sha256,
            decision=("approved" if payload.human_decision == "valid" else "rejected" if payload.human_decision == "invalid" else "unresolved"),
            evidence=(evidence,), attestation=attestation_ref,
        )
        record = c.AuditRecord(
            submission=payload.submission, task=payload.task, verifier=payload.verifier,
            sampling_probability=selection.sampling_probability,
            sampling_kind=selection.sampling_kind, verdict=payload.human_decision,
            human_review=human_review, evidence=(evidence,),
        )
        return payload, record, verification_ref

    def audit(self, run_ids: tuple[str, ...]) -> c.OperationResult:
        if type(run_ids) is not tuple or not run_ids or any(type(item) is not str for item in run_ids):
            raise TypeError("audit run IDs must be a nonempty tuple")
        if len(set(run_ids)) != len(run_ids):
            raise AuditRejected("duplicate audit run ID")
        selections = {item.run_id: item for item in self.selection.selections}
        if set(run_ids) - selections.keys():
            raise AuditRejected("audit run is absent from the frozen selection manifest")
        if set(run_ids) - self.attestations.keys():
            raise AuditRejected("audit run has no detached human adjudication")

        rows = []
        defects: dict[c.ArtifactRef, list[tuple[AuditAdjudication, c.ArtifactRef]]] = {}
        output_evidence = []
        for run_id in run_ids:
            selection = selections[run_id]
            rollout_ref, rollout, _, grade = self._rollout_and_grade(run_id)
            if selection.verifier_decision != self._verifier_decision(grade.disposition):
                raise AuditRejected("frozen audit stratum differs from the selected grade")
            payload, record, verification_ref = self._authenticate(
                selection, self.attestations[run_id], rollout, grade,
            )
            rows.append((selection, payload, record, rollout_ref))
            output_evidence.extend(record.evidence)
            mismatch = (
                selection.verifier_decision == "accepted" and payload.human_decision == "invalid"
            ) or (
                selection.verifier_decision in {"rejected", "invalid"} and payload.human_decision == "valid"
            )
            if mismatch:
                defects.setdefault(payload.verifier, []).append((payload, verification_ref))

        quarantined = []
        affected_runs = set()
        affected_checkpoints = set()
        for verifier, findings in defects.items():
            evidence_refs = tuple(dict.fromkeys(
                ref for payload, verification_ref in findings
                for ref in (payload.counterexample, verification_ref)
            ))
            notice_id = "m8-audit-" + verifier.sha256[:24]
            self.registry.quarantine(
                verifier, notice_id=notice_id,
                reason="authenticated M8 human audit found a verifier decision defect",
                evidence=evidence_refs,
            )
            trace = self.registry.trace(verifier)
            quarantined.append(verifier)
            affected_runs.update(trace.runs)
            affected_checkpoints.update(trace.checkpoints)

        statistics = summarize_audits(tuple(
            AdjudicatedSample(selection=selection, human_decision=payload.human_decision)
            for selection, payload, _, _ in rows
        ))
        actions = () if not defects else (
            "regrade saved submissions with an unaffected verifier",
            "restart training from an unaffected checkpoint or disclose contamination after weight updates",
        )
        report = AuditExecutionReport(
            version="m8-audit-report-v1", population_frame=self.selection.population_frame,
            records=tuple(record for _, _, record, _ in rows), statistics=statistics,
            quarantined_verifiers=tuple(sorted(quarantined, key=lambda ref: ref.sha256)),
            affected_runs=tuple(sorted(affected_runs, key=lambda ref: ref.sha256)),
            affected_checkpoints=tuple(sorted(affected_checkpoints, key=lambda ref: ref.sha256)),
            required_action=actions,
        )
        report_ref = self.store.put_bytes(
            canonical_json(report.model_dump(mode="json")), "m8-audit-report", c.Visibility.PRIVATE,
        )
        evidence = c.EvidenceRecord(
            producer="feature_rl.audits", command=("AuditService.audit", *run_ids),
            recorded_at=datetime.now(timezone.utc), exit_status=0, artifacts=(report_ref,),
            revision=self.revision, scope="human_review",
        )
        return c.OperationResult(
            operation="audit", disposition=c.Disposition.SUCCESS,
            artifacts=(report_ref,), evidence=(evidence, *output_evidence),
            costs=(_unknown_audit_cost(),),
            reason="authenticated human audit completed; report records any quarantine and required remediation",
        )
