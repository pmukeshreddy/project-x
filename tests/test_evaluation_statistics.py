from datetime import datetime, timezone

import pytest

from feature_rl import contracts as c
from test_evaluation_core import ref, study_objects


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def evidence():
    return (
        c.EvidenceRecord(
            producer="unit-diagnostic",
            command=("observed-receipt",),
            recorded_at=NOW,
            exit_status=0,
            artifacts=(ref("receipt", c.Visibility.PRIVATE, "7"),),
            revision="8" * 40,
            scope="unit_diagnostic",
        ),
    )


def observed(prereg, roster, outcomes):
    families = {item.task.sha256: item.repository_family for item in roster.sources}
    values = []
    for assignment in prereg.trials:
        outcome = outcomes[(assignment.task.sha256[0], assignment.arm)]
        valid = outcome in (True, False)
        values.append(
            c.TrialResult(
                trial_id=assignment.trial_id,
                task=assignment.task,
                repository_family=families[assignment.task.sha256],
                arm=assignment.arm,
                policy_seed=assignment.policy_seed,
                case_seed=assignment.case_seed,
                rollout=ref("RolloutRecord", c.Visibility.EVALUATION, assignment.arm.lower()) if valid else None,
                disposition=(c.Disposition.SUCCESS if outcome is True else c.Disposition.REJECTED if outcome is False else c.Disposition.INFRASTRUCTURE),
                resolved=outcome if valid else None,
                evidence=evidence(),
            )
        )
    return tuple(values)


def metric(stats, name):
    return next(item for item in stats.metrics if item.name == name)


def test_statistics_report_all_assigned_valid_only_and_hand_derived_pairs():
    from feature_rl.evaluation import summarize_trials

    roster, prereg, _ = study_objects()
    trials = observed(
        prereg,
        roster,
        {
            ("1", "A"): False, ("1", "B"): True, ("1", "C"): False, ("1", "D"): True,
            ("2", "A"): False, ("2", "B"): False, ("2", "C"): None, ("2", "D"): True,
        },
    )
    stats = summarize_trials(roster, prereg, trials, bootstrap_seed=23, bootstrap_resamples=200)

    c_counts = next(item for item in stats.arms if item.arm == "C")
    assert (c_counts.assigned, c_counts.valid, c_counts.resolved, c_counts.failed, c_counts.invalid) == (2, 1, 0, 1, 1)
    assert metric(stats, "pass_at_1/C/all_assigned").estimate == 0.0
    assert metric(stats, "pass_at_1/D/valid_only").estimate == 1.0
    assert metric(stats, "paired/D-C/task_weighted/all_assigned").estimate == 1.0
    valid_pair = metric(stats, "paired/D-C/task_weighted/valid_only")
    assert valid_pair.estimate == 1.0 and valid_pair.sample_size == 1

    again = summarize_trials(roster, prereg, trials, bootstrap_seed=23, bootstrap_resamples=200)
    assert stats == again


def test_statistics_reject_missing_duplicate_or_assignment_drift():
    from feature_rl.evaluation import StatisticsError, summarize_trials

    roster, prereg, _ = study_objects()
    outcomes = {(task, arm): False for task in ("1", "2") for arm in ("A", "B", "C", "D")}
    trials = observed(prereg, roster, outcomes)
    with pytest.raises(StatisticsError, match="exactly once"):
        summarize_trials(roster, prereg, trials[:-1])
    with pytest.raises(StatisticsError, match="exactly once"):
        summarize_trials(roster, prereg, trials[:-1] + (trials[0],))
    drifted = (trials[0].model_copy(update={"case_seed": 999}),) + trials[1:]
    with pytest.raises(StatisticsError, match="frozen assignment"):
        summarize_trials(roster, prereg, drifted)


def test_all_invalid_has_no_valid_only_estimate_instead_of_zero():
    from feature_rl.evaluation import summarize_trials

    roster, prereg, _ = study_objects()
    trials = observed(prereg, roster, {(task, arm): None for task in ("1", "2") for arm in ("A", "B", "C", "D")})
    stats = summarize_trials(roster, prereg, trials, bootstrap_seed=1, bootstrap_resamples=20)
    valid = metric(stats, "pass_at_1/A/valid_only")
    assert valid.sample_size == 0
    assert valid.estimate is valid.lower is valid.upper is None
    assert metric(stats, "pass_at_1/A/all_assigned").estimate == 0.0


def test_task_and_family_weighted_pairing_have_distinct_hand_derived_denominators():
    from feature_rl.evaluation import SourceAssignment, TrialAssignment, summarize_trials

    roster, prereg, _ = study_objects()
    third = ref("TaskBundle", c.Visibility.EVALUATION, "3")
    roster = roster.model_copy(update={
        "locked_tasks": (*roster.locked_tasks, third),
        "sources": (*roster.sources, SourceAssignment(
            source_id="locked-three", task=third, repository_family="family-one",
            request_lineage=("request-three",), partition=c.Partition.LOCKED_TEST,
            evidence=ref("lineage-proof", c.Visibility.PRIVATE, "9"),
        )),
    })
    extra = tuple(
        TrialAssignment(
            trial_id=f"trial-3-{arm}", task=third, arm=arm,
            policy_seed=11, case_seed=103, episode_index=0,
        )
        for arm in ("A", "B", "C", "D")
    )
    prereg = prereg.model_copy(update={"trials": (*prereg.trials, *extra)})
    outcomes = {(task, arm): False for task in ("1", "2", "3") for arm in ("A", "B", "C", "D")}
    outcomes[("1", "D")] = True
    outcomes[("3", "D")] = True
    stats = summarize_trials(roster, prereg, observed(prereg, roster, outcomes), bootstrap_seed=8, bootstrap_resamples=100)
    assert metric(stats, "paired/D-A/task_weighted/all_assigned").estimate == 2 / 3
    assert metric(stats, "paired/D-A/family_weighted/all_assigned").estimate == 1 / 2
