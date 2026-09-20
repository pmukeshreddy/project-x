import pytest
from pydantic import ValidationError


def sample(run, decision, probability, patch, *, environment="valid", checker="correct", attestation="authenticated"):
    from feature_rl.audits import AdjudicatedPatchSample, PatchAuditSelection

    return AdjudicatedPatchSample(
        selection=PatchAuditSelection(
            subject_id=run * 64, run_id=run * 64,
            repository_family="example-family", stratum=f"{decision}-stratum",
            sampling_probability=probability, verifier_decision=decision,
            failure_category="none" if decision == "accepted" else "checker-rejection",
        ),
        patch_validity=patch, environment_validity=environment,
        checker_assessment=checker, attestation_status=attestation,
    )


def metric(stats, name):
    return next(item for item in stats.metrics if item.name == name)


def test_patch_error_rates_use_the_three_specified_denominators():
    from feature_rl.audits import summarize_audits

    stats = summarize_audits((
        sample("1", "accepted", 1.0, "invalid"),
        sample("2", "rejected", 1.0, "invalid"),
        sample("3", "accepted", 1.0, "valid"),
        sample("4", "accepted", 1.0, "valid"),
        sample("5", "rejected", 1.0, "valid"),
    ))
    assert metric(stats, "invalid_among_accepted").estimate == 1 / 3
    assert metric(stats, "accepted_among_invalid").estimate == 1 / 2
    assert metric(stats, "rejected_among_valid").estimate == 1 / 3


def test_unequal_inclusion_weights_and_unresolved_accounting_are_hand_derived():
    from feature_rl.audits import summarize_audits

    stats = summarize_audits((
        sample("1", "accepted", 0.5, "invalid"),
        sample("2", "rejected", 1.0, "invalid"),
        sample("3", "accepted", 0.25, "valid"),
        sample("4", "rejected", 0.5, "valid"),
        sample("5", "invalid_environment", 1.0, "unresolved", environment="invalid"),
        sample("6", "accepted", 1.0, "unresolved", environment="unresolved", attestation="unavailable"),
    ))
    assert metric(stats, "invalid_among_accepted").estimate == 1 / 3
    assert metric(stats, "accepted_among_invalid").estimate == 2 / 3
    assert metric(stats, "rejected_among_valid").estimate == 1 / 3
    assert stats.accounting.unavailable == 1
    assert stats.accounting.unresolved_subject == 2
    assert stats.accounting.invalid_environment == 1


def test_empty_unadjudicated_denominators_do_not_report_zero():
    from feature_rl.audits import summarize_audits

    stats = summarize_audits((sample("1", "accepted", 1.0, "unresolved", attestation="unavailable"),))
    for name in ("invalid_among_accepted", "accepted_among_invalid", "rejected_among_valid"):
        result = metric(stats, name)
        assert result.sample_size == 0
        assert result.estimate is result.lower is result.upper is None


def test_duplicate_patch_is_rejected_before_denominator_construction():
    from feature_rl.audits import AuditStatisticsError, summarize_audits

    item = sample("1", "accepted", 1.0, "valid")
    with pytest.raises(AuditStatisticsError, match="duplicate"):
        summarize_audits((item, item))


def test_infrastructure_only_valid_patch_cannot_be_a_checker_defect():
    from feature_rl.audits import PatchAuditAdjudication, PatchAuditSelection
    from test_evaluation_core import NOW, ref
    from feature_rl import contracts as c

    selection = PatchAuditSelection(
        subject_id="1" * 64, run_id="1" * 64, repository_family="family",
        stratum="infra", sampling_probability=1.0,
        verifier_decision="invalid_environment", failure_category="infrastructure_failure",
    )
    with pytest.raises(ValidationError, match="infrastructure outcomes|valid environment"):
        PatchAuditAdjudication(
            version="m8-patch-audit-adjudication-v1", sample=selection,
            task=ref("TaskBundle", c.Visibility.EVALUATION, "1"),
            verifier=ref("verifier", c.Visibility.PRIVATE, "2"),
            submission=ref("submission", c.Visibility.PRIVATE, "3"),
            counterexample=ref("counterexample", c.Visibility.PRIVATE, "4"),
            patch_validity="valid", environment_validity="invalid",
            checker_assessment="defect", adjudication="worker infrastructure failed",
            human_identity="reviewer", reviewed_at=NOW,
        )


def test_rejected_source_rate_has_its_own_units_and_denominator():
    from feature_rl import contracts as c
    from feature_rl.audits import AdjudicatedSourceSample, SourceAuditSelection, summarize_source_audits

    def reference(kind, digest, schema=1, encoding="bytes"):
        return c.ArtifactRef(
            sha256=digest * 64, kind=kind, schema_version=schema,
            visibility=c.Visibility.PRIVATE, encoding=encoding,
        )

    candidate = reference("CandidateRecord", "a", 2, "json")
    disposition = reference("m6-source-disposition", "b")
    samples = tuple(
        AdjudicatedSourceSample(
            selection=SourceAuditSelection(
                subject_id=digest * 64, construct_job_id=digest * 64,
                candidate=candidate, source_disposition=disposition,
                repository_family="source-family", failure_category="candidate_rejection",
                stratum="rejected-source", sampling_probability=probability,
            ),
            source_validity=validity, source_assessment="correct",
            attestation_status="authenticated",
        )
        for digest, probability, validity in (("1", 0.5, "valid"), ("2", 0.25, "invalid"))
    )
    stats = summarize_source_audits(samples)
    assert stats.metrics[0].estimate == 1 / 3
    assert stats.accounting.selected == 2
