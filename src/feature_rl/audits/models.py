"""Frozen audit frames, selections, and authenticated adjudication records."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl import contracts as c


VerifierDecision = Literal["accepted", "rejected", "invalid_environment", "invalid_measurement"]
PatchValidity = Literal["valid", "invalid", "unresolved"]
EnvironmentValidity = Literal["valid", "invalid", "unresolved"]
Assessment = Literal["correct", "defect", "unresolved"]
SourceValidity = Literal["valid", "invalid", "unresolved"]


class PatchFrameEntry(c.StrictModel):
    unit: Literal["patch"] = "patch"
    subject_id: c.Digest
    run_id: c.Digest
    repository_family: c.Identifier
    verifier_decision: VerifierDecision
    failure_category: c.Identifier

    @model_validator(mode="after")
    def run_identity(self):
        if self.subject_id != self.run_id:
            raise ValueError("patch subject must be the exact run ID")
        return self


class SourceFrameEntry(c.StrictModel):
    unit: Literal["source"] = "source"
    subject_id: c.Digest
    construct_job_id: c.Digest
    candidate: c.ArtifactRef
    source_disposition: c.ArtifactRef
    repository_family: c.Identifier
    source_status: Literal["rejected_source"] = "rejected_source"
    failure_category: c.Identifier

    @model_validator(mode="after")
    def candidate_identity(self):
        if (
            self.candidate.kind != "CandidateRecord"
            or self.source_disposition.kind != "m6-source-disposition"
            or self.subject_id != self.construct_job_id
        ):
            raise ValueError("source subject must be the exact construct job")
        return self


FrameEntry = Annotated[PatchFrameEntry | SourceFrameEntry, Field(discriminator="unit")]


class AuditPopulationFrame(c.StrictModel):
    version: Literal["m8-audit-population-v1"]
    entries: Annotated[tuple[FrameEntry, ...], Field(min_length=1)]
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def complete_units(self):
        ids = [entry.subject_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate audit population subject")
        if not any(entry.unit == "patch" for entry in self.entries):
            raise ValueError("audit population requires patch units")
        if not any(entry.unit == "source" for entry in self.entries):
            raise ValueError("audit population requires rejected-source units")
        return self


class AuditStratum(c.StrictModel):
    stratum: c.Identifier
    unit: Literal["patch", "source"]
    repository_family: c.Identifier
    status: c.Identifier
    failure_category: c.Identifier
    sample_size: c.PositiveInt


class AuditSamplingPlan(c.StrictModel):
    version: Literal["m8-audit-sampling-v1"]
    population_frame: c.ArtifactRef
    seed: c.NonnegativeInt
    strata: Annotated[tuple[AuditStratum, ...], Field(min_length=1)]
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def unique_strata(self):
        if len({item.stratum for item in self.strata}) != len(self.strata):
            raise ValueError("duplicate audit stratum")
        return self


class PatchAuditSelection(PatchFrameEntry):
    stratum: c.Identifier
    sampling_probability: Annotated[float, Field(gt=0, le=1)]
    sampling_kind: Literal["random"] = "random"


class SourceAuditSelection(SourceFrameEntry):
    stratum: c.Identifier
    sampling_probability: Annotated[float, Field(gt=0, le=1)]
    sampling_kind: Literal["random"] = "random"


AuditSelection = Annotated[PatchAuditSelection | SourceAuditSelection, Field(discriminator="unit")]


class AuditSelectionManifest(c.StrictModel):
    version: Literal["m8-audit-selection-v2"]
    population_frame: c.ArtifactRef
    sampling_plan: c.ArtifactRef
    selections: Annotated[tuple[AuditSelection, ...], Field(min_length=1)]
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def complete_units(self):
        ids = [item.subject_id for item in self.selections]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate selected audit subject")
        decisions = {item.verifier_decision for item in self.selections if item.unit == "patch"}
        if not {"accepted", "rejected"} <= decisions:
            raise ValueError("audit selection requires accepted and rejected patch strata")
        if not any(item.unit == "source" for item in self.selections):
            raise ValueError("audit selection requires a true rejected-source unit")
        return self


class PatchAuditAdjudication(c.StrictModel):
    version: Literal["m8-patch-audit-adjudication-v1"]
    sample: PatchAuditSelection
    task: c.ArtifactRef
    verifier: c.ArtifactRef
    submission: c.ArtifactRef
    counterexample: c.ArtifactRef
    patch_validity: PatchValidity
    environment_validity: EnvironmentValidity
    checker_assessment: Assessment
    adjudication: c.Text
    human_identity: c.Text
    reviewed_at: c.UTCDateTime

    @model_validator(mode="after")
    def coherent_defect(self):
        mismatch = (
            self.sample.verifier_decision == "accepted" and self.patch_validity == "invalid"
        ) or (
            self.sample.verifier_decision == "rejected" and self.patch_validity == "valid"
        )
        if self.checker_assessment == "defect" and (not mismatch or self.environment_validity != "valid"):
            raise ValueError("checker defect requires a valid environment and adjudicated decision mismatch")
        if self.sample.verifier_decision == "invalid_environment" and self.checker_assessment == "defect":
            raise ValueError("infrastructure outcomes cannot establish checker defects")
        return self


class SourceAuditAdjudication(c.StrictModel):
    version: Literal["m8-source-audit-adjudication-v1"]
    sample: SourceAuditSelection
    candidate: c.ArtifactRef
    source_evidence: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1)]
    counterexample: c.ArtifactRef
    source_validity: SourceValidity
    source_assessment: Assessment
    adjudication: c.Text
    human_identity: c.Text
    reviewed_at: c.UTCDateTime

    @model_validator(mode="after")
    def coherent_subject(self):
        if self.candidate != self.sample.candidate:
            raise ValueError("source adjudication candidate differs from selected source")
        if self.source_assessment == "defect" and self.source_validity != "valid":
            raise ValueError("source rejection defect requires a human-valid source")
        return self


class DetachedAuditAttestation(c.StrictModel):
    version: Literal["m8-detached-audit-attestation-v1"]
    payload: c.ArtifactRef
    signature: c.ArtifactRef
    principal: c.Text


class AdjudicatedPatchSample(c.StrictModel):
    selection: PatchAuditSelection
    patch_validity: PatchValidity
    environment_validity: EnvironmentValidity
    checker_assessment: Assessment
    attestation_status: Literal["authenticated", "unavailable"]


class AdjudicatedSourceSample(c.StrictModel):
    selection: SourceAuditSelection
    source_validity: SourceValidity
    source_assessment: Assessment
    attestation_status: Literal["authenticated", "unavailable"]


class AuditAccounting(c.StrictModel):
    selected: c.NonnegativeInt
    authenticated: c.NonnegativeInt
    unavailable: c.NonnegativeInt
    unresolved_subject: c.NonnegativeInt
    invalid_environment: c.NonnegativeInt
    checker_defects: c.NonnegativeInt


class AuditStatistics(c.StrictModel):
    version: Literal["m8-patch-audit-statistics-v2"]
    metrics: Annotated[tuple[c.MetricEstimate, ...], Field(min_length=3, max_length=3)]
    accounting: AuditAccounting
    limitations: tuple[c.Text, ...]


class SourceAuditStatistics(c.StrictModel):
    version: Literal["m8-source-audit-statistics-v1"]
    metrics: Annotated[tuple[c.MetricEstimate, ...], Field(min_length=1, max_length=1)]
    accounting: AuditAccounting
    limitations: tuple[c.Text, ...]


class PatchAuditOutcome(c.StrictModel):
    selection: PatchAuditSelection
    adjudication: PatchAuditAdjudication | None
    record: c.AuditRecord | None
    issue: c.Text


class SourceAuditOutcome(c.StrictModel):
    selection: SourceAuditSelection
    adjudication: SourceAuditAdjudication | None
    evidence: tuple[c.EvidenceRecord, ...]
    issue: c.Text


class AuditExecutionReport(c.StrictModel):
    version: Literal["m8-audit-report-v2"]
    population_frame: c.ArtifactRef
    sampling_plan: c.ArtifactRef
    selection_manifest: c.ArtifactRef
    patch_outcomes: tuple[PatchAuditOutcome, ...]
    source_outcomes: tuple[SourceAuditOutcome, ...]
    patch_statistics: AuditStatistics
    source_statistics: SourceAuditStatistics
    quarantined_verifiers: tuple[c.ArtifactRef, ...]
    quarantined_sources: tuple[c.ArtifactRef, ...]
    affected_runs: tuple[c.ArtifactRef, ...]
    affected_checkpoints: tuple[c.ArtifactRef, ...]
    required_action: tuple[c.Text, ...]
