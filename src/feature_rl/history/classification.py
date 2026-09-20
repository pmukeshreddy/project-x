"""Changed-file categories with uncertain paths retained for qualification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

from feature_rl.contracts import ChangedFile


@dataclass(frozen=True)
class ClassificationResult:
    changed_files: tuple[ChangedFile, ...]
    mixed_paths_for_qualification: tuple[str, ...]


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
        "Makefile",
        "MANIFEST.in",
        "pytest.ini",
        "noxfile.py",
        ".pre-commit-config.yaml",
        ".readthedocs.yaml",
        ".readthedocs.yml",
    }
)


def _automatic(path: str) -> tuple[str, str]:
    item = PurePosixPath(path)
    # Protect build/configuration paths before applying broader directory rules.
    if (item.name in _DEPENDENCY_NAMES
            or (item.name.startswith("requirements") and item.suffix == ".txt")
            or path.startswith(".github/workflows/")):
        return "dependency_build", "Dependency, build, or CI configuration path"
    if (any(part in {"test", "tests"} for part in item.parts[:-1])
            or item.name == "conftest.py" or item.name.startswith("test_")):
        return "tests", "Test or fixture path"
    if (path.startswith(("doc/", "docs/", "example/", "examples/"))
            or path.startswith((".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE/"))
            or item.suffix.lower() in {".md", ".rst"}
            or (item.suffix.lower() in {"", ".txt"} and item.stem.upper() in {
                "README", "CHANGES", "CHANGELOG", "CONTRIBUTING", "AUTHORS", "NEWS"})):
        return "documentation", "Documentation or changelog path"
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
