"""Conservative changed-file categories with explicit mixed-file gates."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

from feature_rl.contracts import ChangedFile


@dataclass(frozen=True)
class ClassificationResult:
    changed_files: tuple[ChangedFile, ...]
    manual_review_required: tuple[str, ...]


_DEPENDENCY_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "uv.lock",
        "poetry.lock",
        "requirements.txt",
        "Dockerfile",
    }
)


def _automatic(path: str) -> tuple[str, str]:
    item = PurePosixPath(path)
    if path.startswith("tests/") or item.name.startswith("test_"):
        return "tests", "Test or fixture path"
    if path.startswith("docs/") or item.suffix.lower() in {".md", ".rst"}:
        return "documentation", "Documentation or changelog path"
    if item.name in _DEPENDENCY_NAMES or path.startswith(".github/workflows/"):
        return "dependency_build", "Dependency, build, or CI configuration path"
    if path.startswith("src/") or item.suffix.lower() in {".py", ".pyi", ".c", ".h"}:
        return "implementation", "Implementation path"
    return "mixed", "Path alone cannot establish a single purpose"


def classify_changed_files(
    paths: tuple[str, ...], *, mixed_paths: Mapping[str, str] | None = None
) -> ClassificationResult:
    mixed_paths = mixed_paths or {}
    if len(set(paths)) != len(paths):
        raise ValueError("changed paths must be unique")
    unknown_overrides = set(mixed_paths).difference(paths)
    if unknown_overrides:
        raise ValueError("mixed path override does not name a changed file")
    files: list[ChangedFile] = []
    unresolved: list[str] = []
    for path in paths:
        if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
            raise ValueError("changed path must be a safe repository-relative path")
        if path in mixed_paths:
            category, rationale = "mixed", mixed_paths[path]
        else:
            category, rationale = _automatic(path)
        files.append(ChangedFile(path=path, category=category, rationale=rationale))
        if category == "mixed":
            unresolved.append(path)
    return ClassificationResult(tuple(files), tuple(sorted(unresolved)))
