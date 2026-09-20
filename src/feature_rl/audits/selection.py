"""Deterministic stratified sampling from a frozen authenticated frame."""
from __future__ import annotations

import hashlib

from feature_rl.artifacts import canonical_json
from feature_rl import contracts as c

from .models import (
    AuditPopulationFrame,
    AuditSamplingPlan,
    AuditSelectionManifest,
    PatchAuditSelection,
    SourceAuditSelection,
)


class AuditSelectionError(ValueError):
    """A frame, stratum plan, or materialized selection is incomplete or drifted."""


def _status(entry) -> str:
    return entry.verifier_decision if entry.unit == "patch" else entry.source_status


def derive_selection_manifest(
    population_ref: c.ArtifactRef,
    population: AuditPopulationFrame,
    plan_ref: c.ArtifactRef,
    plan: AuditSamplingPlan,
) -> AuditSelectionManifest:
    if plan.population_frame != population_ref:
        raise AuditSelectionError("sampling plan references a different population frame")
    grouped = {stratum.stratum: [] for stratum in plan.strata}
    for entry in population.entries:
        matches = [
            stratum for stratum in plan.strata
            if (stratum.unit, stratum.repository_family, stratum.status, stratum.failure_category)
            == (entry.unit, entry.repository_family, _status(entry), entry.failure_category)
        ]
        if len(matches) != 1:
            raise AuditSelectionError("every population unit must match exactly one frozen stratum")
        grouped[matches[0].stratum].append(entry)
    selected = []
    for stratum in plan.strata:
        members = grouped[stratum.stratum]
        if stratum.sample_size > len(members):
            raise AuditSelectionError("stratum sample size exceeds its frozen population")
        ranked = sorted(
            members,
            key=lambda entry: hashlib.sha256(canonical_json({
                "version": "m8-audit-rank-v1", "seed": plan.seed,
                "stratum": stratum.stratum, "subject_id": entry.subject_id,
            })).hexdigest(),
        )
        probability = stratum.sample_size / len(members)
        for entry in ranked[:stratum.sample_size]:
            values = entry.model_dump(mode="python") | {
                "stratum": stratum.stratum,
                "sampling_probability": probability,
                "sampling_kind": "random",
            }
            selected.append(
                PatchAuditSelection(**values) if entry.unit == "patch"
                else SourceAuditSelection(**values)
            )
    return AuditSelectionManifest(
        version="m8-audit-selection-v2", population_frame=population_ref,
        sampling_plan=plan_ref, selections=tuple(selected), created_at=plan.created_at,
    )


def validate_selection_manifest(
    population_ref: c.ArtifactRef,
    population: AuditPopulationFrame,
    plan_ref: c.ArtifactRef,
    plan: AuditSamplingPlan,
    manifest: AuditSelectionManifest,
) -> AuditSelectionManifest:
    expected = derive_selection_manifest(population_ref, population, plan_ref, plan)
    if manifest != expected:
        raise AuditSelectionError("materialized audit selection differs from frozen stratified sampling")
    return manifest
