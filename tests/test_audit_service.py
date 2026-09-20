from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.registry import Registry
from m5_fixtures import task_fixture


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
REVISION = "a" * 40


def unknown_cost():
    return c.CostRecord(
        category="construction", wall_seconds=None, cpu_seconds=None,
        gpu_seconds=None, input_tokens=None, output_tokens=None,
        human_minutes=None, usd=None, measurement="unknown",
        note="unit diagnostic cost is unknown",
    )


def rejected_candidate(store):
    task = store.get_artifact(task_fixture(store))
    pair = store.get_artifact(task.source_pair)
    candidate = store.get_artifact(pair.candidate)
    screening = c.ScreeningDecision(
        disposition=c.Disposition.REJECTED,
        reason="synthetic source rejection for audit mechanics",
        evidence=candidate.screening.evidence,
    )
    return store.put_artifact(candidate.model_copy(update={"screening": screening}))


def test_rejected_source_path_reads_actual_candidate_and_completed_construct_result(tmp_path):
    from feature_rl.audits import AuditRejected, AuditService, SourceFrameEntry
    from feature_rl.pipeline import Factory, read_source_disposition

    store = ArtifactStore(tmp_path / "objects", c.ActorRole.CONTROLLER)
    registry = Registry(tmp_path / "registry", store)
    candidate_ref = rejected_candidate(store)
    candidate = store.get_artifact(candidate_ref)
    result = Factory(store=store, registry=registry, revision=REVISION).screen_source(candidate_ref)
    disposition_ref = result.artifacts[0]
    job_id = read_source_disposition(store, disposition_ref).claim.job_id
    entry = SourceFrameEntry(
        subject_id=job_id, construct_job_id=job_id, candidate=candidate_ref,
        source_disposition=disposition_ref,
        repository_family=candidate.repository_family,
        failure_category=c.Disposition.REJECTED.value,
    )
    service = AuditService.__new__(AuditService)
    service.store, service.registry = store, registry
    job, actual, evidence = service._source_subject(entry)
    assert job.job_id == job_id and actual == candidate and evidence

    with pytest.raises(AuditRejected, match="frame differs"):
        service._source_subject(entry.model_copy(update={"repository_family": "wrong-family"}))


def test_service_registers_opaque_frame_plan_selection_and_configuration_dependencies(tmp_path):
    from feature_rl.audits import (
        AuditPopulationFrame, AuditSamplingPlan, AuditService, AuditStratum,
        PatchFrameEntry, SourceFrameEntry, derive_selection_manifest,
    )
    from feature_rl.qualification.attestation import SSHHumanVerifier

    store = ArtifactStore(tmp_path / "objects", c.ActorRole.CONTROLLER)
    registry = Registry(tmp_path / "registry", store)
    candidate_ref = rejected_candidate(store)
    candidate = store.get_artifact(candidate_ref)
    source_disposition = store.put_bytes(b"frozen source disposition", "m6-source-disposition", c.Visibility.PRIVATE)
    frame = AuditPopulationFrame(
        version="m8-audit-population-v1", created_at=NOW,
        entries=(
            PatchFrameEntry(subject_id="1" * 64, run_id="1" * 64, repository_family="family-one", verifier_decision="accepted", failure_category="none"),
            PatchFrameEntry(subject_id="2" * 64, run_id="2" * 64, repository_family="family-two", verifier_decision="rejected", failure_category=c.Disposition.REJECTED.value),
            SourceFrameEntry(subject_id="3" * 64, construct_job_id="3" * 64, candidate=candidate_ref,
                source_disposition=source_disposition, repository_family=candidate.repository_family,
                failure_category=c.Disposition.REJECTED.value),
        ),
    )
    frame_ref = store.put_bytes(canonical_json(frame.model_dump(mode="json")), "m8-audit-population", c.Visibility.PRIVATE)
    plan = AuditSamplingPlan(
        version="m8-audit-sampling-v1", population_frame=frame_ref, seed=9, created_at=NOW,
        strata=(
            AuditStratum(stratum="accepted", unit="patch", repository_family="family-one", status="accepted", failure_category="none", sample_size=1),
            AuditStratum(stratum="rejected", unit="patch", repository_family="family-two", status="rejected", failure_category=c.Disposition.REJECTED.value, sample_size=1),
            AuditStratum(stratum="source", unit="source", repository_family=candidate.repository_family, status="rejected_source", failure_category=c.Disposition.REJECTED.value, sample_size=1),
        ),
    )
    plan_ref = store.put_bytes(canonical_json(plan.model_dump(mode="json")), "m8-audit-sampling", c.Visibility.PRIVATE)
    selection = derive_selection_manifest(frame_ref, frame, plan_ref, plan)
    selection_ref = store.put_bytes(canonical_json(selection.model_dump(mode="json")), "m8-audit-selection", c.Visibility.PRIVATE)
    verifier = SSHHumanVerifier(enrollment_path="/nonexistent", expected_enrollment_sha256="f" * 64)
    service = AuditService(
        store=store, registry=registry, human_verifier=verifier,
        selection_manifest=selection_ref, attestations={}, revision=REVISION,
    )
    traced = set(registry.trace(candidate_ref).artifacts)
    assert {candidate_ref, frame_ref, plan_ref, selection_ref, service.configuration} <= traced

    # No human material is available: every selected unit remains explicit and
    # the provisional operation is still durably selected in the real Registry.
    diagnostic = store.put_bytes(b"diagnostic audit dependency", "audit-diagnostic", c.Visibility.PRIVATE)
    service._population_subjects = lambda: {
        item.subject_id: (
            (diagnostic, SimpleNamespace(task=diagnostic), diagnostic,
             SimpleNamespace(submission=diagnostic, verifier=diagnostic), None)
            if item.unit == "patch" else
            (SimpleNamespace(result=SimpleNamespace(artifacts=(diagnostic,), evidence=())), None, (candidate_ref,))
        )
        for item in selection.selections
    }
    run_ids = tuple(sorted(item.run_id for item in selection.selections if item.unit == "patch"))
    result = service.audit(run_ids)
    assert result.disposition == c.Disposition.PROVISIONAL
    assert registry.enqueue(service._spec()).result == result
    from feature_rl.audits import AuditExecutionReport
    from feature_rl.verifiers.loader import read_local
    report = read_local(store, result.artifacts[0], AuditExecutionReport, "m8-audit-report")
    assert report.patch_statistics.accounting.unavailable == 2
    assert report.source_statistics.accounting.unavailable == 1
