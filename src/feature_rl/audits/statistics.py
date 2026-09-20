"""Inverse-probability weighted patch and source audit estimates."""
from __future__ import annotations

import math
from typing import Callable, TypeVar

from feature_rl import contracts as c

from .models import (
    AdjudicatedPatchSample,
    AdjudicatedSourceSample,
    AuditAccounting,
    AuditStatistics,
    SourceAuditStatistics,
)


class AuditStatisticsError(ValueError):
    """Audit samples cannot support the requested population estimate."""


Sample = TypeVar("Sample", AdjudicatedPatchSample, AdjudicatedSourceSample)


def _weighted_metric(
    samples: tuple[Sample, ...], *, name: str,
    denominator: Callable[[Sample], bool], event: Callable[[Sample], bool],
) -> c.MetricEstimate:
    relevant = [item for item in samples if item.attestation_status == "authenticated" and denominator(item)]
    limitations = (
        "Hájek inverse-probability ratio assumes the frozen frame and recorded inclusion probabilities",
        "Wilson interval uses inverse-probability effective sample size",
        "unavailable and unresolved selected samples are accounted separately and excluded from adjudicated denominators",
    )
    if not relevant:
        return c.MetricEstimate(
            name=name, estimate=None, lower=None, upper=None, sample_size=0,
            method="no authenticated adjudications in denominator", limitations=limitations,
        )
    weights = [1.0 / item.selection.sampling_probability for item in relevant]
    events = [float(event(item)) for item in relevant]
    estimate = sum(weight * outcome for weight, outcome in zip(weights, events)) / sum(weights)
    effective_n = sum(weights) ** 2 / sum(weight * weight for weight in weights)
    z = 1.959963984540054
    scale = 1 + z * z / effective_n
    center = (estimate + z * z / (2 * effective_n)) / scale
    radius = z * math.sqrt(estimate * (1 - estimate) / effective_n + z * z / (4 * effective_n * effective_n)) / scale
    return c.MetricEstimate(
        name=name, estimate=estimate, lower=max(0.0, center - radius),
        upper=min(1.0, center + radius), sample_size=len(relevant),
        method="inverse-probability weighted ratio with effective-n Wilson interval",
        limitations=limitations,
    )


def _accounting(samples) -> AuditAccounting:
    return AuditAccounting(
        selected=len(samples),
        authenticated=sum(item.attestation_status == "authenticated" for item in samples),
        unavailable=sum(item.attestation_status == "unavailable" for item in samples),
        unresolved_subject=sum(
            getattr(item, "patch_validity", getattr(item, "source_validity", None)) == "unresolved"
            for item in samples
        ),
        invalid_environment=sum(getattr(item, "environment_validity", None) == "invalid" for item in samples),
        checker_defects=sum(
            getattr(item, "checker_assessment", getattr(item, "source_assessment", None)) == "defect"
            for item in samples
        ),
    )


def summarize_audits(samples: tuple[AdjudicatedPatchSample, ...]) -> AuditStatistics:
    if type(samples) is not tuple:
        raise TypeError("adjudicated patch samples must be a tuple")
    ids = [item.selection.subject_id for item in samples]
    if len(ids) != len(set(ids)):
        raise AuditStatisticsError("duplicate audited patch")
    metrics = (
        _weighted_metric(
            samples, name="invalid_among_accepted",
            denominator=lambda item: item.selection.verifier_decision == "accepted" and item.patch_validity != "unresolved",
            event=lambda item: item.patch_validity == "invalid",
        ),
        _weighted_metric(
            samples, name="accepted_among_invalid",
            denominator=lambda item: item.patch_validity == "invalid",
            event=lambda item: item.selection.verifier_decision == "accepted",
        ),
        _weighted_metric(
            samples, name="rejected_among_valid",
            denominator=lambda item: item.patch_validity == "valid",
            event=lambda item: item.selection.verifier_decision == "rejected",
        ),
    )
    return AuditStatistics(
        version="m8-patch-audit-statistics-v2", metrics=metrics, accounting=_accounting(samples),
        limitations=(
            "patch validity, environment validity, and checker defects are separate adjudications",
            "mutation rejection and rejected-source audits are outside patch denominators",
        ),
    )


def summarize_source_audits(samples: tuple[AdjudicatedSourceSample, ...]) -> SourceAuditStatistics:
    if type(samples) is not tuple:
        raise TypeError("adjudicated source samples must be a tuple")
    ids = [item.selection.subject_id for item in samples]
    if len(ids) != len(set(ids)):
        raise AuditStatisticsError("duplicate audited source")
    metric = _weighted_metric(
        samples, name="valid_source_among_rejected_sources",
        denominator=lambda item: item.source_validity != "unresolved",
        event=lambda item: item.source_validity == "valid",
    )
    return SourceAuditStatistics(
        version="m8-source-audit-statistics-v1", metrics=(metric,), accounting=_accounting(samples),
        limitations=("source units and denominators are separate from submitted-patch audits",),
    )
