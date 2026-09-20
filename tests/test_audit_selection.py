from datetime import datetime, timezone

import pytest

from feature_rl import contracts as c


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def opaque(kind, digest):
    return c.ArtifactRef(
        sha256=digest * 64, kind=kind, schema_version=1,
        visibility=c.Visibility.PRIVATE, encoding="bytes",
    )


def candidate(digest):
    return c.ArtifactRef(
        sha256=digest * 64, kind="CandidateRecord", schema_version=2,
        visibility=c.Visibility.PRIVATE, encoding="json",
    )


def frame_and_plan():
    from feature_rl.audits import (
        AuditPopulationFrame, AuditSamplingPlan, AuditStratum,
        PatchFrameEntry, SourceFrameEntry,
    )

    population_ref = opaque("m8-audit-population", "a")
    plan_ref = opaque("m8-audit-sampling", "b")
    frame = AuditPopulationFrame(
        version="m8-audit-population-v1", created_at=NOW,
        entries=(
            PatchFrameEntry(subject_id="1" * 64, run_id="1" * 64, repository_family="family-one", verifier_decision="accepted", failure_category="none"),
            PatchFrameEntry(subject_id="2" * 64, run_id="2" * 64, repository_family="family-one", verifier_decision="accepted", failure_category="none"),
            PatchFrameEntry(subject_id="3" * 64, run_id="3" * 64, repository_family="family-two", verifier_decision="rejected", failure_category="candidate-rejection"),
            SourceFrameEntry(subject_id="4" * 64, construct_job_id="4" * 64, candidate=candidate("c"),
                source_disposition=opaque("m6-source-disposition", "d"),
                repository_family="family-two", failure_category="candidate-rejection"),
        ),
    )
    plan = AuditSamplingPlan(
        version="m8-audit-sampling-v1", population_frame=population_ref,
        seed=31, created_at=NOW,
        strata=(
            AuditStratum(stratum="accepted-family-one", unit="patch", repository_family="family-one", status="accepted", failure_category="none", sample_size=1),
            AuditStratum(stratum="rejected-family-two", unit="patch", repository_family="family-two", status="rejected", failure_category="candidate-rejection", sample_size=1),
            AuditStratum(stratum="source-family-two", unit="source", repository_family="family-two", status="rejected_source", failure_category="candidate-rejection", sample_size=1),
        ),
    )
    return population_ref, frame, plan_ref, plan


def test_stratified_selection_is_seeded_complete_and_has_derived_probabilities():
    from feature_rl.audits import derive_selection_manifest, validate_selection_manifest

    args = frame_and_plan()
    first = derive_selection_manifest(*args)
    second = derive_selection_manifest(*args)
    assert first == second
    accepted = next(item for item in first.selections if item.unit == "patch" and item.verifier_decision == "accepted")
    assert accepted.sampling_probability == 0.5
    assert {item.unit for item in first.selections} == {"patch", "source"}
    assert validate_selection_manifest(*args, first) == first


def test_selection_rejects_omitted_frame_strata_and_probability_tampering():
    from feature_rl.audits import AuditSelectionError, derive_selection_manifest, validate_selection_manifest

    population_ref, frame, plan_ref, plan = frame_and_plan()
    with pytest.raises(AuditSelectionError, match="exactly one"):
        derive_selection_manifest(population_ref, frame, plan_ref, plan.model_copy(update={"strata": plan.strata[:-1]}))
    manifest = derive_selection_manifest(population_ref, frame, plan_ref, plan)
    rows = list(manifest.selections)
    rows[0] = rows[0].model_copy(update={"sampling_probability": 1.0})
    with pytest.raises(AuditSelectionError, match="differs"):
        validate_selection_manifest(population_ref, frame, plan_ref, plan, manifest.model_copy(update={"selections": tuple(rows)}))
