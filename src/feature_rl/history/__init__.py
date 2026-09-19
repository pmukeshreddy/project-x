"""Git history reconstruction and changed-file classification."""
from .classification import ClassificationResult, classify_changed_files
from .git import (
    CommitObject,
    GitHistory,
    HistoryError,
    HistoryOutputLimit,
    Reconstruction,
    UnrecoverableHistory,
)

__all__ = [
    "ClassificationResult",
    "CommitObject",
    "GitHistory",
    "HistoryError",
    "HistoryOutputLimit",
    "Reconstruction",
    "UnrecoverableHistory",
    "classify_changed_files",
]
