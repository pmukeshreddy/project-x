"""Inverse-probability weighted audit defect estimates."""
from __future__ import annotations

import math

from feature_rl import contracts as c

from .models import AdjudicatedSample, AuditStatistics, VerifierDecision


class AuditStatisticsError(ValueError):
    """Audit samples cannot support the requested population estimate."""


def _weighted_metric(
    samples: tuple[AdjudicatedSample, ...],
    *,
    name: str,
    denominator: VerifierDecision,
    event: str,
) -> c.MetricEstimate:
    relevant = [
        item for item in samples
        if item.selection.sampling_kind == "random"
        and item.selection.verifier_decision == denominator
        and item.human_decision != "unresolved"
    ]
    limitations = (
        "Horvitz-Thompson ratio estimate assumes recorded inclusion probabilities and authenticated adjudications",
        "Wilson interval uses inverse-probability effective sample size",
        "targeted defect-discovery samples are excluded from population estimates",
    )
    if not relevant:
        return c.MetricEstimate(
            name=name, estimate=None, lower=None, upper=None, sample_size=0,
            method="no authenticated adjudications in denominator", limitations=limitations,
        )
    weights = [1.0 / item.selection.sampling_probability for item in relevant]
    events = [float(item.human_decision == event) for item in relevant]
    estimate = sum(weight * outcome for weight, outcome in zip(weights, events)) / sum(weights)
    effective_n = sum(weights) ** 2 / sum(weight * weight for weight in weights)
    z = 1.959963984540054
    denominator_value = 1 + z * z / effective_n
    center = (estimate + z * z / (2 * effective_n)) / denominator_value
    radius = z * math.sqrt(estimate * (1 - estimate) / effective_n + z * z / (4 * effective_n * effective_n)) / denominator_value
    return c.MetricEstimate(
        name=name, estimate=estimate, lower=max(0.0, center - radius),
        upper=min(1.0, center + radius), sample_size=len(relevant),
        method="inverse-probability weighted ratio with effective-n Wilson interval",
        limitations=limitations,
    )


def summarize_audits(samples: tuple[AdjudicatedSample, ...]) -> AuditStatistics:
    if type(samples) is not tuple:
        raise TypeError("adjudicated audit samples must be a tuple")
    run_ids = [item.selection.run_id for item in samples]
    if len(run_ids) != len(set(run_ids)):
        raise AuditStatisticsError("duplicate audited run")
    metrics = (
        _weighted_metric(samples, name="invalid_among_accepted", denominator="accepted", event="invalid"),
        _weighted_metric(samples, name="accepted_among_invalid", denominator="invalid", event="valid"),
        _weighted_metric(samples, name="rejected_among_valid", denominator="rejected", event="valid"),
    )
    return AuditStatistics(
        version="m8-audit-statistics-v1", metrics=metrics,
        limitations=(
            "mutation rejection is outside these three human-adjudicated error rates",
            "empty and unresolved strata produce no estimate rather than a zero error rate",
        ),
    )
