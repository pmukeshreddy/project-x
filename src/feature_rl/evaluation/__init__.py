"""Evaluation freezing, statistics, and execution services."""
from .freeze import FrozenStudyError, LineageLeakage, validate_lineage_freeze, validate_preregistration
from .models import (
    AdaptationFunnel,
    AdaptationStage,
    ArmProtocol,
    BudgetLimit,
    EvaluationPreregistration,
    FrozenRoster,
    LineageRelation,
    SourceAssignment,
    TrialAssignment,
)
from .statistics import ArmCounts, EvaluationStatistics, StatisticsError, summarize_trials
from .adaptation import (
    AdaptationBatch,
    AdaptationItem,
    ExternalAdaptationConfig,
    ExternalCorpusAdapter,
    ExternalCorpusFrame,
    ExternalCorpusRow,
    ExternalOriginMapping,
    ExternalSourceAssignment,
)

__all__ = [
    "AdaptationFunnel", "AdaptationStage", "ArmProtocol", "BudgetLimit",
    "EvaluationPreregistration", "FrozenRoster", "FrozenStudyError",
    "LineageLeakage", "LineageRelation", "SourceAssignment", "TrialAssignment",
    "ArmCounts", "EvaluationStatistics", "StatisticsError", "summarize_trials",
    "AdaptationBatch", "AdaptationItem", "ExternalAdaptationConfig",
    "ExternalCorpusAdapter", "ExternalCorpusFrame", "ExternalCorpusRow",
    "ExternalOriginMapping", "ExternalSourceAssignment",
    "validate_lineage_freeze", "validate_preregistration",
]
