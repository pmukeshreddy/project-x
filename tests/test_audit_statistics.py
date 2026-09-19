from feature_rl import contracts as c


def sample(run, decision, probability, human):
    from feature_rl.audits import AdjudicatedSample, AuditSelection

    return AdjudicatedSample(
        selection=AuditSelection(
            run_id=run * 64,
            repository_family="example-family",
            source_status="accepted_source",
            stratum=f"{decision}-stratum",
            sampling_probability=probability,
            sampling_kind="random",
            verifier_decision=decision,
            failure_category="none" if decision == "accepted" else "checker-rejection",
        ),
        human_decision=human,
    )


def metric(stats, name):
    return next(item for item in stats.metrics if item.name == name)


def test_weighted_audit_estimate_uses_known_inclusion_probabilities():
    from feature_rl.audits import summarize_audits

    stats = summarize_audits(
        (
            sample("1", "accepted", 0.5, "invalid"),
            sample("2", "accepted", 0.25, "valid"),
            sample("3", "invalid", 1.0, "valid"),
            sample("4", "rejected", 0.5, "valid"),
        )
    )
    assert metric(stats, "invalid_among_accepted").estimate == 1 / 3
    assert metric(stats, "accepted_among_invalid").estimate == 1.0
    assert metric(stats, "rejected_among_valid").estimate == 1.0


def test_empty_or_unadjudicated_stratum_does_not_report_zero_error():
    from feature_rl.audits import summarize_audits

    stats = summarize_audits((sample("1", "accepted", 1.0, "unresolved"),))
    for name in ("invalid_among_accepted", "accepted_among_invalid", "rejected_among_valid"):
        result = metric(stats, name)
        assert result.sample_size == 0
        assert result.estimate is result.lower is result.upper is None


def test_duplicate_audit_run_is_rejected_before_denominator_construction():
    import pytest
    from feature_rl.audits import AuditStatisticsError, summarize_audits

    item = sample("1", "accepted", 1.0, "valid")
    with pytest.raises(AuditStatisticsError, match="duplicate"):
        summarize_audits((item, item))


def test_targeted_findings_are_not_misreported_as_population_estimates():
    from feature_rl.audits import summarize_audits

    item = sample("1", "accepted", 0.1, "invalid")
    item = item.model_copy(update={
        "selection": item.selection.model_copy(update={"sampling_kind": "targeted"}),
    })
    estimate = metric(summarize_audits((item,)), "invalid_among_accepted")
    assert estimate.sample_size == 0 and estimate.estimate is None
