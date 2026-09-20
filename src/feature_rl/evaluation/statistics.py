"""Honest accounting and family-clustered evaluation summaries."""
from __future__ import annotations

import random
from statistics import mean
from typing import Annotated, Literal

from pydantic import Field

from feature_rl import contracts as c

from .models import EvaluationPreregistration, FrozenRoster


class StatisticsError(ValueError):
    """Observed results do not match the preregistered trial ledger."""


class ArmCounts(c.StrictModel):
    arm: Literal["A", "B", "C", "D", "E"]
    assigned: c.NonnegativeInt
    valid: c.NonnegativeInt
    resolved: c.NonnegativeInt
    failed: c.NonnegativeInt
    invalid: c.NonnegativeInt


class EvaluationStatistics(c.StrictModel):
    version: Literal["m8-evaluation-statistics-v1"]
    arms: Annotated[tuple[ArmCounts, ...], Field(min_length=1)]
    metrics: Annotated[tuple[c.MetricEstimate, ...], Field(min_length=1)]
    bootstrap_seed: c.NonnegativeInt
    bootstrap_resamples: c.PositiveInt
    limitations: tuple[c.Text, ...]


def _key(ref: c.ArtifactRef) -> tuple[str, str, int]:
    return ref.sha256, ref.kind, ref.schema_version


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _cluster_interval(
    groups: dict[str, list[float]], *, seed: int, resamples: int, family_weighted: bool,
) -> tuple[float | None, float | None]:
    families = sorted(groups)
    if len(families) < 2:
        return None, None
    generator = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        selected = [families[generator.randrange(len(families))] for _ in families]
        if family_weighted:
            samples.append(mean(mean(groups[family]) for family in selected))
        else:
            values = [value for family in selected for value in groups[family]]
            samples.append(mean(values))
    return _percentile(samples, 0.025), _percentile(samples, 0.975)


def _estimate(
    name: str,
    groups: dict[str, list[float]],
    *,
    family_weighted: bool,
    sample_size: int,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    limitations: tuple[str, ...],
) -> c.MetricEstimate:
    values = [value for family in sorted(groups) for value in groups[family]]
    if not values:
        return c.MetricEstimate(
            name=name, estimate=None, lower=None, upper=None, sample_size=0,
            method="no valid observations", limitations=limitations,
        )
    estimate = mean(mean(values) for values in groups.values()) if family_weighted else mean(values)
    lower, upper = _cluster_interval(
        groups, seed=bootstrap_seed, resamples=bootstrap_resamples,
        family_weighted=family_weighted,
    )
    method = "family-clustered percentile bootstrap"
    metric_limits = list(limitations)
    if len(groups) < 2:
        metric_limits.append("fewer than two repository families; uncertainty interval unavailable")
    return c.MetricEstimate(
        name=name, estimate=estimate, lower=lower, upper=upper,
        sample_size=sample_size, method=method, limitations=tuple(metric_limits),
    )


def summarize_trials(
    roster: FrozenRoster,
    preregistration: EvaluationPreregistration,
    trials: tuple[c.TrialResult, ...],
    *,
    bootstrap_seed: int = 0,
    bootstrap_resamples: int = 2000,
) -> EvaluationStatistics:
    """Summarize a complete supplied receipt ledger; this does not execute trials."""
    if type(trials) is not tuple:
        raise TypeError("observed trials must be a tuple")
    if type(bootstrap_seed) is not int or bootstrap_seed < 0:
        raise ValueError("bootstrap seed must be a nonnegative integer")
    if type(bootstrap_resamples) is not int or bootstrap_resamples < 1:
        raise ValueError("bootstrap resamples must be positive")

    assignments = {item.trial_id: item for item in preregistration.trials}
    if len(trials) != len(assignments) or len({item.trial_id for item in trials}) != len(trials) or {item.trial_id for item in trials} != set(assignments):
        raise StatisticsError("every assigned trial must appear exactly once")
    families = {_key(item.task): item.repository_family for item in roster.sources}
    observed: dict[str, c.TrialResult] = {}
    for item in trials:
        assignment = assignments[item.trial_id]
        expected_family = families.get(_key(assignment.task))
        if (
            _key(item.task) != _key(assignment.task)
            or item.arm != assignment.arm
            or item.policy_seed != assignment.policy_seed
            or item.case_seed != assignment.case_seed
            or item.repository_family != expected_family
        ):
            raise StatisticsError(f"trial {item.trial_id} differs from its frozen assignment")
        observed[item.trial_id] = item

    limitations = [
        "statistics aggregate supplied observed receipts and do not establish task or policy admission",
        "all-assigned rates count invalid and unresolved assigned trials as unsuccessful",
        "independently trained replicates are not represented; policy sampling seeds do not measure training variability",
    ]
    metrics: list[c.MetricEstimate] = []
    counts: list[ArmCounts] = []
    protocols = sorted(item.arm for item in preregistration.arms)
    metric_name = "pass_at_1" if preregistration.metric == "pass_at_1" else f"best_of_{preregistration.episodes_per_trial}"
    grouped: dict[tuple[tuple[str, str, int], int, str], list[c.TrialResult]] = {}
    for assignment in preregistration.trials:
        grouped.setdefault((_key(assignment.task), assignment.policy_seed, assignment.arm), []).append(observed[assignment.trial_id])
    units: dict[tuple[tuple[str, str, int], int, str], tuple[str, float, float | None]] = {}
    for key, episodes in grouped.items():
        if len(episodes) != preregistration.episodes_per_trial:
            raise StatisticsError("metric unit does not contain the frozen episode count")
        family = episodes[0].repository_family
        all_value = float(any(item.resolved is True for item in episodes))
        valid_value = all_value if all(item.resolved is not None for item in episodes) else None
        units[key] = family, all_value, valid_value
    for arm_index, arm in enumerate(protocols):
        arm_units = [value for (*_, unit_arm), value in units.items() if unit_arm == arm]
        valid = [item for item in arm_units if item[2] is not None]
        successes = [item for item in valid if item[2]]
        counts.append(ArmCounts(
            arm=arm, assigned=len(arm_units), valid=len(valid), resolved=len(successes),
            failed=len(valid) - len(successes), invalid=len(arm_units) - len(valid),
        ))
        all_groups: dict[str, list[float]] = {}
        valid_groups: dict[str, list[float]] = {}
        for family, all_value, valid_value in arm_units:
            all_groups.setdefault(family, []).append(all_value)
            if valid_value is not None:
                valid_groups.setdefault(family, []).append(valid_value)
        metrics.append(_estimate(
            f"{metric_name}/{arm}/all_assigned", all_groups, family_weighted=False,
            sample_size=len(arm_units), bootstrap_seed=bootstrap_seed + arm_index * 2,
            bootstrap_resamples=bootstrap_resamples, limitations=tuple(limitations),
        ))
        metrics.append(_estimate(
            f"{metric_name}/{arm}/valid_only", valid_groups, family_weighted=False,
            sample_size=len(valid), bootstrap_seed=bootstrap_seed + arm_index * 2 + 1,
            bootstrap_resamples=bootstrap_resamples,
            limitations=tuple((*limitations, "invalid trials excluded from this denominator")),
        ))

    for comparison_index, (left, right) in enumerate(preregistration.comparisons):
        all_groups: dict[str, list[float]] = {}
        valid_groups: dict[str, list[float]] = {}
        cells = sorted({(task, seed) for task, seed, _ in units})
        for task, seed in cells:
            left_family, left_all, left_valid = units[(task, seed, left)]
            right_family, right_all, right_valid = units[(task, seed, right)]
            if left_family != right_family:
                raise StatisticsError("paired arms disagree on repository family")
            all_groups.setdefault(left_family, []).append(right_all - left_all)
            if left_valid is not None and right_valid is not None:
                valid_groups.setdefault(left_family, []).append(right_valid - left_valid)
        for population, groups in (("all_assigned", all_groups), ("valid_only", valid_groups)):
            n = sum(map(len, groups.values()))
            population_limits = tuple(limitations) if population == "all_assigned" else tuple((*limitations, "pairs with either invalid trial excluded from this denominator"))
            for weighting_index, family_weighted in enumerate((False, True)):
                weighting = "family_weighted" if family_weighted else "task_weighted"
                metrics.append(_estimate(
                    f"paired/{right}-{left}/{weighting}/{population}", groups,
                    family_weighted=family_weighted,
                    sample_size=len(groups) if family_weighted else n,
                    bootstrap_seed=bootstrap_seed + 100 + comparison_index * 4 + weighting_index,
                    bootstrap_resamples=bootstrap_resamples,
                    limitations=population_limits,
                ))

    return EvaluationStatistics(
        version="m8-evaluation-statistics-v1", arms=tuple(counts), metrics=tuple(metrics),
        bootstrap_seed=bootstrap_seed, bootstrap_resamples=bootstrap_resamples,
        limitations=tuple(limitations),
    )
