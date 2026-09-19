"""Frozen audit selection and human adjudication records."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl import contracts as c


VerifierDecision = Literal["accepted", "rejected", "invalid"]
HumanDecision = Literal["valid", "invalid", "unresolved"]


class AuditSelection(c.StrictModel):
    run_id: c.Digest
    repository_family: c.Identifier
    source_status: Literal["accepted_source", "rejected_source"]
    stratum: c.Identifier
    sampling_probability: Annotated[float, Field(gt=0, le=1)]
    sampling_kind: Literal["random", "targeted"]
    verifier_decision: VerifierDecision
    failure_category: c.Identifier


class AuditSelectionManifest(c.StrictModel):
    version: Literal["m8-audit-selection-v1"]
    population_frame: c.ArtifactRef
    selections: Annotated[tuple[AuditSelection, ...], Field(min_length=1)]
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def unique_runs(self):
        if len({item.run_id for item in self.selections}) != len(self.selections):
            raise ValueError("duplicate selected run")
        decisions = {item.verifier_decision for item in self.selections}
        if not {"accepted", "rejected"} <= decisions:
            raise ValueError("audit frame requires accepted and rejected verifier strata")
        if not any(item.source_status == "rejected_source" for item in self.selections):
            raise ValueError("audit frame requires a rejected-source stratum")
        return self


class AuditAdjudication(c.StrictModel):
    version: Literal["m8-audit-adjudication-v1"]
    sample: AuditSelection
    run_id: c.Digest
    task: c.ArtifactRef
    verifier: c.ArtifactRef
    submission: c.ArtifactRef
    counterexample: c.ArtifactRef
    human_decision: HumanDecision
    adjudication: c.Text
    human_identity: c.Text
    reviewed_at: c.UTCDateTime

    @model_validator(mode="after")
    def sample_identity(self):
        if self.sample.run_id != self.run_id:
            raise ValueError("signed audit sample and run identity differ")
        return self


class DetachedAuditAttestation(c.StrictModel):
    version: Literal["m8-detached-audit-attestation-v1"]
    payload: c.ArtifactRef
    signature: c.ArtifactRef
    principal: c.Text


class AdjudicatedSample(c.StrictModel):
    selection: AuditSelection
    human_decision: HumanDecision


class AuditStatistics(c.StrictModel):
    version: Literal["m8-audit-statistics-v1"]
    metrics: Annotated[tuple[c.MetricEstimate, ...], Field(min_length=3, max_length=3)]
    limitations: tuple[c.Text, ...]


class AuditExecutionReport(c.StrictModel):
    version: Literal["m8-audit-report-v1"]
    population_frame: c.ArtifactRef
    records: Annotated[tuple[c.AuditRecord, ...], Field(min_length=1)]
    statistics: AuditStatistics
    quarantined_verifiers: tuple[c.ArtifactRef, ...]
    affected_runs: tuple[c.ArtifactRef, ...]
    affected_checkpoints: tuple[c.ArtifactRef, ...]
    required_action: tuple[c.Text, ...]
