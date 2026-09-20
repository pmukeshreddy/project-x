"""Pinned local Transformers/PyTorch authoring configuration."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import Field

from feature_rl.contracts import Digest, Revision, StrictModel, Text
from ._worker import checked_manifest, dependency_versions, model_files


class BackendConfigurationError(RuntimeError):
    """The local backend differs from its configured immutable manifests."""


class PlatformFacts(StrictModel):
    implementation: str
    python_major: int
    python_minor: int
    system: str
    machine: str
    macos_major: int

    @classmethod
    def current(cls) -> "PlatformFacts":
        version = platform.mac_ver()[0]
        return cls(
            implementation=sys.implementation.name,
            python_major=sys.version_info.major,
            python_minor=sys.version_info.minor,
            system=platform.system(),
            machine=platform.machine(),
            macos_major=int(version.split(".", 1)[0]) if version else 0,
        )


class VerifiedModel(StrictModel):
    model_id: str
    revision: str
    file_count: int
    total_bytes: int
    config_sha256: Digest
    tokenizer_sha256: Digest
    weights_sha256: Digest


class VerifiedDependencies(StrictModel):
    package_count: int
    versions: dict[str, str]


class VerifiedBackend(StrictModel):
    platform: PlatformFacts
    model: VerifiedModel
    dependencies: VerifiedDependencies
    model_manifest_sha256: Digest
    dependency_manifest_sha256: Digest
    device: str = "cpu"
    dtype: str = "float32"


def verify_platform(facts: PlatformFacts) -> PlatformFacts:
    if (
        facts.implementation != "cpython"
        or (facts.python_major, facts.python_minor) < (3, 11)
        or facts.system not in {"Linux", "Darwin"}
    ):
        raise BackendConfigurationError("local authoring requires CPython 3.11+ on Linux or macOS")
    return facts


def verify_dependency_manifest(
    manifest: object, installed_versions: Mapping[str, str] | None = None,
) -> VerifiedDependencies:
    try:
        versions = dependency_versions(manifest, installed_versions)
    except (ValueError, OSError) as error:
        raise BackendConfigurationError(str(error)) from error
    return VerifiedDependencies(package_count=len(versions), versions=versions)


def verify_model_files(manifest: object, model_directory: Path) -> VerifiedModel:
    try:
        return VerifiedModel(**model_files(manifest, model_directory))
    except (ValueError, OSError) as error:
        raise BackendConfigurationError(str(error)) from error


class BackendConfig(StrictModel):
    python_executable: Path
    model_directory: Path
    model_manifest: Path
    dependency_manifest: Path
    model_id: Text
    revision: Revision
    model_manifest_sha256: Digest
    dependency_manifest_sha256: Digest
    device: Annotated[str, Field(pattern=r"^(cpu|cuda:[0-9]+)$")] = "cpu"
    dtype: Literal["float32", "float16", "bfloat16"] = "float32"

    def verify(self) -> VerifiedBackend:
        if not self.python_executable.is_file():
            raise BackendConfigurationError("missing configured Python executable")
        if self.python_executable.resolve() != Path(sys.executable).resolve():
            raise BackendConfigurationError(
                "worker interpreter must match the provider interpreter whose closure was verified"
            )
        facts = verify_platform(PlatformFacts.current())
        try:
            manifest = checked_manifest(self.model_manifest, self.model_manifest_sha256)
            dependencies = checked_manifest(self.dependency_manifest, self.dependency_manifest_sha256)
        except (OSError, ValueError) as error:
            raise BackendConfigurationError(str(error)) from error
        if (manifest.get("model_id"), manifest.get("revision")) != (self.model_id, self.revision):
            raise BackendConfigurationError("model manifest differs from configured identity")
        return VerifiedBackend(
            platform=facts,
            model=verify_model_files(manifest, self.model_directory),
            dependencies=verify_dependency_manifest(dependencies),
            model_manifest_sha256=self.model_manifest_sha256,
            dependency_manifest_sha256=self.dependency_manifest_sha256,
            device=self.device,
            dtype=self.dtype,
        )
