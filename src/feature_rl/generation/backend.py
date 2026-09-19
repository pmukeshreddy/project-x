"""Fail-closed verification for the one qualified local MLX backend."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Mapping

from feature_rl.contracts import Digest, StrictModel

MODEL_ID = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
MODEL_REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
MODEL_CONFIG_SHA256 = "574349e5a343236546fda55e4744a76e181f534182d7dc60ff1bad7e7a502849"
MODEL_MANIFEST_SHA256 = "697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0"
DEPENDENCY_MANIFEST_SHA256 = "d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831"
MODEL_FILES = frozenset({
    "added_tokens.json", "chat_template.jinja", "config.json", "generation_config.json",
    "merges.txt", "model.safetensors", "model.safetensors.index.json",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
})
EXPECTED_DEPENDENCIES = {
    "annotated-doc": "0.0.5", "anyio": "4.15.1", "certifi": "2026.7.22",
    "click": "8.5.0", "filelock": "4.0.1", "fsspec": "2026.9.0", "h11": "0.16.0",
    "hf-xet": "1.6.0", "httpcore": "1.0.9", "httpx": "0.28.1",
    "huggingface-hub": "1.32.0", "idna": "3.20", "jinja2": "3.1.6",
    "markdown-it-py": "4.2.0", "markupsafe": "3.0.3", "mdurl": "0.1.2",
    "mlx": "0.32.2", "mlx-lm": "0.31.3", "mlx-metal": "0.32.2",
    "numpy": "2.5.3", "packaging": "26.3", "protobuf": "7.36.2",
    "pygments": "2.21.0", "pyyaml": "6.0.3", "regex": "2026.9.10",
    "rich": "15.0.0", "safetensors": "0.8.0", "sentencepiece": "0.2.2",
    "shellingham": "1.5.4", "tokenizers": "0.23.2", "tqdm": "4.70.1",
    "transformers": "5.17.0", "typer": "0.27.2", "typing-extensions": "4.16.0",
}


class BackendConfigurationError(RuntimeError):
    """The installed backend differs from the reviewed closure."""


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
        try:
            macos_major = int(version.split(".", 1)[0])
        except (ValueError, IndexError):
            macos_major = 0
        return cls(
            implementation=sys.implementation.name,
            python_major=sys.version_info.major,
            python_minor=sys.version_info.minor,
            system=platform.system(),
            machine=platform.machine(),
            macos_major=macos_major,
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


def _normalized(name: str) -> str:
    return name.lower().replace("_", "-")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_platform(facts: PlatformFacts) -> PlatformFacts:
    if (
        facts.implementation != "cpython"
        or (facts.python_major, facts.python_minor) != (3, 13)
        or facts.system != "Darwin"
        or facts.machine != "arm64"
        or facts.macos_major < 26
    ):
        raise BackendConfigurationError(
            "backend requires CPython 3.13 on Darwin arm64 with macOS 26 or newer"
        )
    return facts


def verify_dependency_manifest(
    manifest: object, installed_versions: Mapping[str, str]
) -> VerifiedDependencies:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("wheels"), list):
        raise BackendConfigurationError("dependency manifest has an invalid shape")
    described: dict[str, str] = {}
    for record in manifest["wheels"]:
        if not isinstance(record, dict):
            raise BackendConfigurationError("dependency manifest contains a non-object record")
        try:
            name = _normalized(record["name"])
            version, filename, sha256 = record["version"], record["filename"], record["sha256"]
        except KeyError as error:
            raise BackendConfigurationError("dependency manifest record is incomplete") from error
        if name in described or not isinstance(version, str):
            raise BackendConfigurationError("dependency manifest contains duplicate or invalid packages")
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            raise BackendConfigurationError("dependency closure must contain wheels only")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise BackendConfigurationError("dependency wheel hash is invalid")
        described[name] = version
    if described != EXPECTED_DEPENDENCIES:
        raise BackendConfigurationError("dependency manifest is not the exact approved closure")
    actual = {_normalized(name): value for name, value in installed_versions.items()}
    for name, version in EXPECTED_DEPENDENCIES.items():
        if actual.get(name) != version:
            raise BackendConfigurationError(
                f"installed dependency {name} must be exactly {version}; got {actual.get(name)!r}"
            )
    return VerifiedDependencies(package_count=len(described), versions=described)


def verify_model_files(manifest: object, model_directory: Path) -> VerifiedModel:
    if not isinstance(manifest, dict):
        raise BackendConfigurationError("model manifest has an invalid shape")
    if manifest.get("model_id") != MODEL_ID or manifest.get("revision") != MODEL_REVISION:
        raise BackendConfigurationError("model identity differs from the approved revision")
    if set(manifest.get("runtime_positive_allowlist", ())) != MODEL_FILES:
        raise BackendConfigurationError("model manifest allowlist differs from the approved set")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise BackendConfigurationError("model manifest files must be a list")
    by_path: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise BackendConfigurationError("model manifest contains an invalid file record")
        name = record["path"]
        if name in by_path:
            raise BackendConfigurationError("model manifest contains duplicate file paths")
        by_path[name] = record
    if set(by_path) != MODEL_FILES:
        raise BackendConfigurationError("model manifest files differ from the positive allowlist")
    actual_names = {path.name for path in model_directory.iterdir()}
    if actual_names != MODEL_FILES:
        raise BackendConfigurationError("model directory violates the positive allowlist")
    total = 0
    for name, record in by_path.items():
        path = model_directory / name
        if path.is_symlink() or not path.is_file():
            raise BackendConfigurationError(f"model file {name} must be a regular non-symlink file")
        size = path.stat().st_size
        if size != record.get("bytes"):
            raise BackendConfigurationError(f"model file {name} size/hash identity does not match")
        if _sha256(path) != record.get("sha256"):
            raise BackendConfigurationError(f"model file {name} hash does not match the manifest")
        total += size
    if total != manifest.get("actual_total_bytes"):
        raise BackendConfigurationError("model total bytes do not match the manifest")
    try:
        config = json.loads((model_directory / "config.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise BackendConfigurationError("model config is not valid JSON") from error
    if config.get("model_type") != "qwen3" or config.get("architectures") != ["Qwen3ForCausalLM"]:
        raise BackendConfigurationError("model config architecture is not the approved Qwen3 model")
    if "auto_map" in config:
        raise BackendConfigurationError("remote-code auto_map is forbidden")
    return VerifiedModel(
        model_id=MODEL_ID,
        revision=MODEL_REVISION,
        file_count=len(by_path),
        total_bytes=total,
        config_sha256=by_path["config.json"]["sha256"],
        tokenizer_sha256=by_path["tokenizer.json"]["sha256"],
        weights_sha256=by_path["model.safetensors"]["sha256"],
    )


class BackendConfig(StrictModel):
    python_executable: Path
    model_directory: Path
    model_manifest: Path
    dependency_manifest: Path

    def verify(self) -> VerifiedBackend:
        for label, path, directory in (
            ("python executable", self.python_executable, False),
            ("model directory", self.model_directory, True),
            ("model manifest", self.model_manifest, False),
            ("dependency manifest", self.dependency_manifest, False),
        ):
            invalid_type = path.is_dir() if not directory else not path.is_dir()
            if not path.exists() or invalid_type:
                raise BackendConfigurationError(f"missing or invalid {label}: {path}")
        if self.model_directory.is_symlink() or self.model_manifest.is_symlink() or self.dependency_manifest.is_symlink():
            raise BackendConfigurationError("model and manifest paths must not be symlinks")
        model_digest, dependency_digest = _sha256(self.model_manifest), _sha256(self.dependency_manifest)
        if model_digest != MODEL_MANIFEST_SHA256 or dependency_digest != DEPENDENCY_MANIFEST_SHA256:
            raise BackendConfigurationError("manifest identity differs from the reviewed manifests")
        try:
            model_manifest = json.loads(self.model_manifest.read_text())
            dependency_manifest = json.loads(self.dependency_manifest.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise BackendConfigurationError("manifest is not valid JSON") from error
        if self.python_executable.resolve() != Path(sys.executable).resolve():
            raise BackendConfigurationError(
                "configured worker interpreter must match the provider interpreter whose closure was verified"
            )
        installed: dict[str, str] = {}
        for name in EXPECTED_DEPENDENCIES:
            try:
                installed[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                installed[name] = "<missing>"
        return VerifiedBackend(
            platform=verify_platform(PlatformFacts.current()),
            model=verify_model_files(model_manifest, self.model_directory),
            dependencies=verify_dependency_manifest(dependency_manifest, installed),
            model_manifest_sha256=model_digest,
            dependency_manifest_sha256=dependency_digest,
        )
