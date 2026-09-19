"""Risk-based tests for the configured local generation provider."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from unittest.mock import patch

import pytest
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainValidator,
    ValidationInfo,
    ValidationError,
    WrapValidator,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticOmit, core_schema

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import (
    ActorRole,
    ArtifactRef,
    RequirementContract,
    ScenarioPlan,
    StrictModel,
    UTCDateTime,
    Visibility,
)
from feature_rl.generation import (
    AuthoringContext,
    BackendConfigurationError,
    BackendConfig,
    GenerationLimits,
    GenerationRequest,
    GenerationStage,
    PlatformFacts,
    verify_dependency_manifest,
    verify_model_files,
    verify_platform,
    BoundedProcessRunner,
    GenerationProviderError,
    LocalGenerationProvider,
    ProcessOutcome,
    VerifiedBackend,
    VerifiedDependencies,
    VerifiedModel,
)
import feature_rl.generation.runner as runner_module
from feature_rl.generation.provider import _decode_event

ARCHIVE_NAMES = (
    "attempt", "request", "response", "retrieval", "schema", "options",
    "provenance", "usage", "cost", "events", "status",
)


def artifact(kind: str, visibility: Visibility = Visibility.AUTHORING) -> ArtifactRef:
    return ArtifactRef(
        sha256="a" * 64,
        kind=kind,
        schema_version=1,
        visibility=visibility,
        encoding="bytes",
    )


def context(
    *,
    context_id: str = "SRC_1",
    role: str = "request",
    kind: str = "request-snapshot",
    visibility: Visibility = Visibility.AUTHORING,
) -> AuthoringContext:
    return AuthoringContext(
        context_id=context_id,
        role=role,
        source=artifact(kind, visibility),
        locator="issue.body",
        text="Add an observable behavior.",
        provenance_label="historical_request",
    )


def limits(**updates) -> GenerationLimits:
    values = dict(
        measurement_profile="tiny_smoke_2048x128",
        wall_seconds=120.0,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        file_size_bytes=1_048_576,
        input_tokens=2048,
        output_tokens=128,
        mlx_memory_guideline_bytes=3_758_096_384,
        mlx_wired_limit_bytes=3_758_096_384,
        mlx_cache_limit_bytes=0,
        physical_footprint_kill_bytes=4_294_967_296,
        physical_footprint_poll_seconds=0.02,
        declared_memory_ceiling_bytes=5_368_709_120,
    )
    return GenerationLimits(**(values | updates))


def request(
    *, stage=GenerationStage.INITIAL_AUTHORING, contexts=None,
    generation_limits: GenerationLimits | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        request_id="REQ_CALL_1",
        response_id="RESP_1",
        prompt_id="PROMPT_1",
        stage=stage,
        system_prompt="Return only the required JSON envelope.",
        instruction="Extract the requested structured data.",
        contexts=tuple(contexts or (context(),)),
        allowed_requirement_ids=("FEATURE_1",),
        limits=generation_limits or limits(),
        seed=0,
    )


def test_initial_authoring_rejects_private_or_reference_context():
    """Removing the stage allowlist would expose H/private reference material."""
    for forbidden in (
        context(role="baseline", kind="reference-tree"),
        context(kind="RequirementContract", visibility=Visibility.PRIVATE),
    ):
        with pytest.raises(ValidationError):
            request(contexts=(forbidden,))


def test_checker_stage_accepts_only_explicit_contract_and_scenario_context():
    """Broadening checker input beyond frozen contract/scenario data is a leak."""
    admitted = (
        context(role="contract", kind="RequirementContract", visibility=Visibility.PRIVATE),
        context(
            context_id="SRC_2",
            role="scenario",
            kind="ScenarioPlan",
            visibility=Visibility.EVALUATION,
        ),
        context(
            context_id="SRC_3", role="public_check", kind="public-check",
            visibility=Visibility.PUBLIC,
        ),
    )
    built = request(stage=GenerationStage.CHECKER_GENERATION, contexts=admitted)
    assert tuple(item.role for item in built.contexts) == ("contract", "scenario", "public_check")
    with pytest.raises(ValidationError):
        request(stage=GenerationStage.CHECKER_GENERATION, contexts=(context(role="baseline"),))


def test_scenario_planning_requires_one_authoring_contract_and_admitted_evidence():
    """The first plan is grounded in one frozen contract without admitting H or a plan."""
    contract = context(
        context_id="CONTRACT_1",
        role="contract",
        kind="RequirementContract",
        visibility=Visibility.AUTHORING,
    ).model_copy(update={"source": artifact("RequirementContract").model_copy(update={"encoding": "json"})})
    built = request(
        stage=GenerationStage.SCENARIO_PLANNING,
        contexts=(context(), context(context_id="B_1", role="baseline"), contract),
    )
    assert tuple(item.role for item in built.contexts) == ("request", "baseline", "contract")

    invalid_context_sets = (
        (context(),),
        (contract, contract.model_copy(update={"context_id": "CONTRACT_2"})),
        (
            contract.model_copy(
                update={"source": artifact("ScenarioPlan").model_copy(update={"encoding": "json"})}
            ),
        ),
        (
            contract.model_copy(
                update={
                    "source": artifact(
                        "RequirementContract", Visibility.PRIVATE
                    ).model_copy(update={"encoding": "json"})
                }
            ),
        ),
        (
            contract,
            context(
                context_id="PLAN_1",
                role="scenario",
                kind="ScenarioPlan",
                visibility=Visibility.PRIVATE,
            ),
        ),
        (contract, context(context_id="H_1", role="baseline", kind="reference-tree")),
    )
    for contexts in invalid_context_sets:
        with pytest.raises(ValidationError):
            request(stage=GenerationStage.SCENARIO_PLANNING, contexts=contexts)


def test_request_rejects_duplicate_ids_placeholders_and_relaxed_resource_policy():
    """IDs must be fixed before inference and the qualified bounds cannot drift."""
    with pytest.raises(ValidationError):
        request(contexts=(context(), context()))
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(request().model_dump() | {"response_id": "{{response_id}}"})
    for update in (
        {"output_tokens": 129},
        {"input_tokens": 2049},
        {"wall_seconds": 120.1},
        {"cpu_seconds": 121},
        {"file_size_bytes": 1_048_577},
        {"physical_footprint_poll_seconds": 0.021},
        {"physical_footprint_kill_bytes": 5_368_709_120},
    ):
        with pytest.raises(ValidationError):
            limits(**update)
    larger = limits(
        measurement_profile="larger_unqualified", input_tokens=8192, output_tokens=1024
    )
    assert larger.input_tokens == 8192


def test_request_actual_serialization_is_strict_and_stable():
    """Serialization drift would break immutable request identity."""
    encoded = request().model_dump_json()
    decoded = json.loads(encoded)
    assert decoded["request_id"] == "REQ_CALL_1"
    assert decoded["contexts"][0]["source"]["visibility"] == "authoring"
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(decoded | {"unexpected": True})


def test_generation_identity_lengths_and_public_boundary_revalidation():
    values = request().model_dump()
    for field in ("request_id", "response_id", "prompt_id"):
        with pytest.raises(ValidationError):
            GenerationRequest.model_validate(values | {field: "R" * 129})
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(values | {"request_id": "R" * 1_048_577})
    maximum = GenerationRequest.model_validate(
        values
        | {
            "request_id": "R" * 128,
            "response_id": "S" * 128,
            "prompt_id": "P" * 128,
        }
    )
    assert len(maximum.request_id) == len(maximum.response_id) == len(maximum.prompt_id) == 128

    runner = FakeRunner()
    archive_calls = []
    provider = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=lambda *args: archive_calls.append(args),
        runner=runner,
    )
    base = request()
    constructed_baseline = GenerationRequest.model_construct(**base.__dict__)
    assert GenerationRequest.model_validate(constructed_baseline) == base
    bypassed_requests = (
        base.model_copy(update={"request_id": "R" * 129}),
        GenerationRequest.model_construct(
            **(base.__dict__ | {"prompt_id": "P" * 129})
        ),
    )
    for bypassed in bypassed_requests:
        with pytest.raises(ValidationError):
            provider.generate(bypassed, SmokeContent)
    assert archive_calls == []
    assert runner.calls == []


MODEL_FILES = (
    "added_tokens.json",
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "model.safetensors.index.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


def synthetic_model(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    files = []
    for name in MODEL_FILES:
        data = (
            b'{"architectures":["Qwen3ForCausalLM"],"model_type":"qwen3"}'
            if name == "config.json"
            else ("content:" + name).encode()
        )
        (model / name).write_bytes(data)
        files.append(
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "published_sha256": None,
                "url": "https://example.invalid/" + name,
            }
        )
    manifest = {
        "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
        "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
        "runtime_positive_allowlist": list(MODEL_FILES),
        "actual_total_bytes": sum(item["bytes"] for item in files),
        "files": files,
    }
    return model, manifest


def test_model_verifier_rejects_extra_missing_and_tampered_files(tmp_path):
    """A changed model byte or broadened file set must block before inference."""
    model, manifest = synthetic_model(tmp_path)
    verified = verify_model_files(manifest, model)
    assert verified.tokenizer_sha256 == next(
        item["sha256"] for item in manifest["files"] if item["path"] == "tokenizer.json"
    )

    (model / "remote_model.py").write_text("raise RuntimeError('must not run')")
    with pytest.raises(BackendConfigurationError, match="allowlist"):
        verify_model_files(manifest, model)
    (model / "remote_model.py").unlink()
    (model / "tokenizer.json").write_text("tampered")
    with pytest.raises(BackendConfigurationError, match="hash"):
        verify_model_files(manifest, model)


def test_platform_and_dependency_closure_refuse_fallbacks():
    """Unsupported hosts or package drift must not select another backend."""
    supported = PlatformFacts(
        implementation="cpython",
        python_major=3,
        python_minor=13,
        system="Darwin",
        machine="arm64",
        macos_major=26,
    )
    assert verify_platform(supported) == supported
    with pytest.raises(BackendConfigurationError, match="CPython 3.13"):
        verify_platform(supported.model_copy(update={"machine": "x86_64"}))

    manifest = json.loads(Path("docs/evidence/M2/local-mlx-dependencies.json").read_text())
    installed = {item["name"]: item["version"] for item in manifest["wheels"]}
    assert verify_dependency_manifest(manifest, installed).package_count == 34
    installed["mlx"] = "0.0.0"
    with pytest.raises(BackendConfigurationError, match="installed dependency"):
        verify_dependency_manifest(manifest, installed)


def test_backend_config_requires_existing_exact_manifests(tmp_path):
    """Missing configuration and manifest substitution must fail closed."""
    missing = BackendConfig(
        python_executable=tmp_path / "python",
        model_directory=tmp_path / "model",
        model_manifest=tmp_path / "model.json",
        dependency_manifest=tmp_path / "dependencies.json",
    )
    with pytest.raises(BackendConfigurationError, match="missing"):
        missing.verify()

    for path in (
        missing.python_executable,
        missing.model_manifest,
        missing.dependency_manifest,
    ):
        path.write_text("{}")
    missing.model_directory.mkdir()
    with pytest.raises(BackendConfigurationError, match="manifest identity"):
        missing.verify()


def test_runner_enforces_combined_output_cap_and_cleans_process_group(tmp_path):
    """Unbounded event output must be killed and retained bytes stay bounded."""
    outcome = BoundedProcessRunner().run(
        command=(sys.executable, "-c", "import sys; sys.stdout.write('x'*200000); sys.stdout.flush()"),
        stdin=b"",
        cwd=tmp_path,
        environment={"PATH": os.environ["PATH"]},
        limits=limits(output_bytes=4096),
    )
    assert outcome.termination == "output_cap"
    assert len(outcome.stdout) + len(outcome.stderr) <= 4096
    assert outcome.process_group_cleanup_verified is True


def test_runner_enforces_deadline_and_records_resource_policy(tmp_path):
    """A stuck worker must not survive its external wall deadline."""
    outcome = BoundedProcessRunner().run(
        command=(sys.executable, "-c", "import time; time.sleep(10)"),
        stdin=b"",
        cwd=tmp_path,
        environment={"PATH": os.environ["PATH"]},
        limits=limits(wall_seconds=0.1),
    )
    assert outcome.termination == "deadline"
    assert outcome.wall_seconds < 2
    assert outcome.cpu_limit_enforcement == "kernel_rlimit_cpu"
    assert outcome.file_size_limit_enforcement == "kernel_rlimit_fsize"
    assert outcome.memory_limit_enforcement == "sampled_proc_pid_rusage_20ms"
    assert outcome.process_group_cleanup_verified is True


def test_runner_monitor_exception_returns_partial_outcome_after_cleanup(tmp_path):
    """A watchdog exception must not escape while its owned child remains alive."""
    with patch.object(
        runner_module, "_footprint", side_effect=OSError("injected observation failure")
    ):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "monitoring_failure"
    assert outcome.monitoring_failures == 1
    assert outcome.monitor_error_type == "OSError"
    assert outcome.process_group_cleanup_verified is True
    assert outcome.exit_status is not None


def test_runner_single_lost_watchdog_observation_fails_closed(tmp_path):
    """A later successful sample cannot erase an earlier watchdog gap."""
    with patch.object(runner_module, "_footprint", return_value=None):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "monitoring_failure"
    assert outcome.monitoring_failures == 1
    assert outcome.memory_samples == 0
    assert outcome.process_group_cleanup_verified is True


def test_runner_interruption_still_cleans_and_returns_partial_outcome(tmp_path):
    """Controller interruption is classified only after bounded group cleanup."""
    with patch.object(runner_module, "_footprint", side_effect=KeyboardInterrupt):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "interrupted"
    assert outcome.monitoring_failures == 1
    assert outcome.monitor_error_type == "KeyboardInterrupt"
    assert outcome.process_group_cleanup_verified is True
    assert outcome.exit_status is not None


class SmokeContent(StrictModel):
    ok: bool
    nonce: str


class StructuredContent(StrictModel):
    values: tuple[str, ...]
    visibility: Visibility
    recorded_at: datetime


class LiteralLeaf(StrictModel):
    confirmed: Literal[True]
    version: Literal[3]


class NestedLiteralContent(StrictModel):
    leaf: LiteralLeaf


class EnabledLiteral(StrictModel):
    flag: Literal[True]


class DisabledLiteral(StrictModel):
    flag: Literal[False]


class UnionLiteralContent(StrictModel):
    mode: EnabledLiteral | DisabledLiteral


class NumberAlternative(StrictModel):
    flag: int


class MixedLiteralContent(StrictModel):
    mode: EnabledLiteral | NumberAlternative


class ScalarBooleanUnionContent(StrictModel):
    flag: Literal[True] | int


class ScalarIntegerUnionContent(StrictModel):
    value: Literal[3] | float


class NestedScalarUnionContent(StrictModel):
    values: tuple[Literal[True] | int, ...]


class JsonModeLiteralContent(StrictModel):
    flag: Literal["ready"]

    @model_validator(mode="after")
    def require_json_mode(self, info: ValidationInfo):
        if info.mode != "json":
            raise ValueError("JSON validation mode required")
        return self


class EnumLiteralContent(StrictModel):
    visibility: Literal[Visibility.AUTHORING]
    values: tuple[str, ...]
    recorded_at: UTCDateTime


class DeclinedLiteralAlternative(StrictModel):
    flag: Literal[True]

    @model_validator(mode="after")
    def decline(self):
        raise ValueError("declined alternative")


class AllowedBooleanAlternative(StrictModel):
    flag: bool


class CallbackUnionContent(StrictModel):
    mode: DeclinedLiteralAlternative | AllowedBooleanAlternative


class DecliningPostInitAlternative(StrictModel):
    flag: Literal[True]

    def model_post_init(self, _context):
        raise ValueError("declined in post init")


class PostInitUnionContent(StrictModel):
    mode: DecliningPostInitAlternative | AllowedBooleanAlternative


class BeforeFieldContent(StrictModel):
    flag: Literal[True]
    number: int

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, value):
        return int(value) if isinstance(value, str) else value


def _normalize_wrapped_integer(value, handler):
    return handler(int(value) if isinstance(value, str) else value)


class WrapFieldContent(StrictModel):
    flag: Literal[True]
    number: Annotated[int, WrapValidator(_normalize_wrapped_integer)]


class ReferencedBeforeLeaf(StrictModel):
    number: int

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, value):
        return int(value) if isinstance(value, str) else value


class ReferencedBeforeContent(StrictModel):
    flag: Literal[True]
    first: ReferencedBeforeLeaf
    second: ReferencedBeforeLeaf


class NormalizedInteger:
    @classmethod
    def __get_pydantic_core_schema__(cls, _source, _handler):
        return core_schema.chain_schema(
            [
                core_schema.no_info_after_validator_function(
                    int, core_schema.str_schema()
                ),
                core_schema.int_schema(),
            ]
        )


class AfterChainNumericLiteralContent(StrictModel):
    flag: Literal[True]
    number: NormalizedInteger


class AfterChainStringLiteralContent(StrictModel):
    flag: Literal["ready"]
    number: NormalizedInteger


OMIT_CALLBACKS: list[str] = []


def _omit_skip(value):
    OMIT_CALLBACKS.append(value)
    if value == "skip":
        raise PydanticOmit
    return value


class OmitNumericLiteralContent(StrictModel):
    flag: Literal[True]
    values: Annotated[
        list[Annotated[str, AfterValidator(_omit_skip)]], Field(max_length=1)
    ]


class OmitStringLiteralContent(StrictModel):
    flag: Literal["ready"]
    values: Annotated[
        list[Annotated[str, AfterValidator(_omit_skip)]], Field(max_length=1)
    ]


class GuardedSetLeaf(StrictModel):
    flag: Literal[True]
    label: str


class GuardedModelSetContent(StrictModel):
    values: Annotated[set[GuardedSetLeaf], Field(min_length=2)]


class GuardedModelFrozenSetContent(StrictModel):
    values: Annotated[frozenset[GuardedSetLeaf], Field(min_length=2)]


class GuardedSetPayload(StrictModel):
    values: Annotated[set[GuardedSetLeaf], Field(min_length=2)]


class GuardedFrozenSetPayload(StrictModel):
    values: Annotated[frozenset[GuardedSetLeaf], Field(min_length=2)]


class NestedGuardedModelSetContent(StrictModel):
    payload: GuardedSetPayload


class NestedGuardedModelFrozenSetContent(StrictModel):
    payload: GuardedFrozenSetPayload


class NumericLiteralDefaultContent(StrictModel):
    flag: Literal[True]
    marker: str = "generated"


class NumericKeyLiteralMappingContent(StrictModel):
    flags: dict[int, Literal[True]]


class UnsupportedDecimalLiteralContent(StrictModel):
    value: Literal[Decimal("1")]


class ExtraAllowLeaf(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    x: int


class ExtraAllowNestedContent(StrictModel):
    flag: Literal[True]
    item: ExtraAllowLeaf


class ExtraAllowRootContent(StrictModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="allow")
    flag: Literal[True]
    x: int


class ExtraIgnoreLeaf(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    x: int


class ExtraIgnoreNestedContent(StrictModel):
    flag: Literal[True]
    item: ExtraIgnoreLeaf


class ExtraForbidLeaf(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    x: int


class ExtraForbidNestedContent(StrictModel):
    flag: Literal[True]
    item: ExtraForbidLeaf


class PatternedLiteralContent(StrictModel):
    flags: dict[Annotated[str, Field(pattern=r"^k_")], Literal[True]]


class UnsupportedPlainLiteralContent(StrictModel):
    flag: Annotated[
        Literal[True],
        PlainValidator(lambda value: value, json_schema_input_type=Literal[True]),
    ]


class FakeBackend:
    python_executable = Path(sys.executable)
    model_directory = Path("/explicit/model")
    model_manifest = Path("/explicit/model-manifest.json")
    dependency_manifest = Path("/explicit/dependencies.json")

    def verify(self):
        return VerifiedBackend(
            platform=PlatformFacts(
                implementation="cpython", python_major=3, python_minor=13,
                system="Darwin", machine="arm64", macos_major=26,
            ),
            model=VerifiedModel(
                model_id="mlx-community/Qwen3-4B-Instruct-2507-4bit",
                revision="50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
                file_count=11,
                total_bytes=2_278_969_697,
                config_sha256="574349e5a343236546fda55e4744a76e181f534182d7dc60ff1bad7e7a502849",
                tokenizer_sha256="aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
                weights_sha256="2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f",
            ),
            dependencies=VerifiedDependencies(package_count=34, versions={"mlx": "0.32.2"}),
            model_manifest_sha256="697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0",
            dependency_manifest_sha256="d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831",
        )


class NeverVerifiedBackend(FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        raise AssertionError("schema refusal must precede backend verification")


def worker_events(*, event_override=None, truncated=False):
    common = {
        "protocol_version": 3,
        "request_id": "REQ_CALL_1",
        "response_id": "RESP_1",
        "prompt_id": "PROMPT_1",
    }
    events = [
        common | {
            "event": "identity_validated",
            "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
            "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
            "config_sha256": "574349e5a343236546fda55e4744a76e181f534182d7dc60ff1bad7e7a502849",
            "tokenizer_sha256": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
            "weights_sha256": "2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f",
            "model_manifest_sha256": "697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0",
            "dependency_manifest_sha256": "d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831",
            "dependency_versions": {"mlx": "0.32.2"},
            "seed": 0,
            "prompt_sha256": "AUTO",
            "worker_source_sha256": "AUTO",
            "offline_environment": {
                "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
            },
            "local_files_only": True,
            "remote_code": False,
        },
        common | {"event": "input_accepted", "actual_input_tokens": 41, "input_token_ids": list(range(41)), "max_input_tokens": 2048},
        common | {
            "event": "memory_controls_set",
            "mlx_memory_guideline_bytes": 3_758_096_384,
            "mlx_cache_limit_bytes": 0,
            "mlx_wired_limit_bytes": 3_758_096_384,
            "previous_memory_limit_bytes": 5_000_000_000,
            "previous_cache_limit_bytes": 5_000_000_000,
            "previous_wired_limit_bytes": 0,
        },
        common | {
            "event": "model_loaded",
            "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
            "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
            "fresh_process": True, "fresh_prompt_cache": True,
            "active_memory_bytes": 2_000_000_000,
            "peak_memory_bytes": 2_100_000_000,
            "cache_memory_bytes": 0,
        },
        common | {"event": "token", "position": 1, "token_id": 100, "selected_model_logprob": -0.25, "text_fragment": "{"},
        common | {
            "event": "completed",
            "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
            "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
            "input_tokens": 41, "output_tokens": 1, "max_output_tokens": 128,
            "finish_reason": "stop", "sampling_policy": "greedy_argmax",
            "truncated": truncated,
            "output_text": json.dumps({"response_id":"RESP_1","source_ids":["SRC_1"],"requirement_ids":["FEATURE_1"],"content":{"ok":True,"nonce":"unit"}}),
            "inference_seconds": 0.5, "total_seconds": 1.0,
            "active_memory_bytes": 2_050_000_000,
            "peak_memory_bytes": 2_100_000_000,
            "cache_memory_bytes": 0,
            "fresh_process": True, "fresh_prompt_cache": True,
        },
    ]
    if event_override is not None:
        events.insert(-1, event_override)
    return ("\n".join(json.dumps(item) for item in events) + "\n").encode()


class FakeRunner:
    def __init__(self, stdout=None, termination="process_exit", exit_status=0):
        self.stdout = worker_events() if stdout is None else stdout
        self.termination = termination
        self.exit_status = exit_status
        self.calls = []

    def run(self, **values):
        self.calls.append(values)
        stdout = self.stdout
        try:
            events = [json.loads(line) for line in stdout.splitlines()]
            sent = json.loads(values["stdin"])
            if events and events[0].get("prompt_sha256") == "AUTO":
                events[0]["prompt_sha256"] = hashlib.sha256(sent["prompt"].encode()).hexdigest()
            if events and events[0].get("worker_source_sha256") == "AUTO":
                events[0]["worker_source_sha256"] = hashlib.sha256(
                    Path("src/feature_rl/generation/_worker.py").read_bytes()
                ).hexdigest()
            stdout = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return ProcessOutcome(
            termination=self.termination,
            exit_status=self.exit_status,
            wall_seconds=1.5,
            cpu_seconds=1.0,
            stdout=stdout,
            stderr=b"",
            memory_samples=5,
            max_sampled_physical_footprint_bytes=2_000_000_000,
            max_reported_lifetime_physical_footprint_bytes=2_100_000_000,
            breach_sample=None,
            process_group_cleanup_verified=True,
            monitoring_failures=1 if self.termination == "monitoring_failure" else 0,
            monitor_error_type=(
                "ObservationUnavailable" if self.termination == "monitoring_failure" else None
            ),
            monitor_error=(
                "proc_pid_rusage returned no observation"
                if self.termination == "monitoring_failure" else None
            ),
        )


def test_provider_validates_envelope_and_archives_complete_call(tmp_path):
    """A successful call retains all input, output, attribution, usage and cost facts."""
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(backend=FakeBackend(), archive=store.put_bytes, runner=runner)
    result = provider.generate(request(), SmokeContent)
    assert result.content == SmokeContent(ok=True, nonce="unit")
    assert result.usage.token_ids == (100,)
    assert result.usage.input_token_ids == tuple(range(41))
    assert result.usage.selected_model_logprobs == (-0.25,)
    assert result.usage.sampling_policy == "greedy_argmax"
    assert result.usage.behavior_logprobs is None
    assert result.cost.measurement == "partial"
    assert result.record.success is True
    assert set(result.record.archives) == {
        "attempt", "request", "response", "retrieval", "schema", "options", "provenance",
        "usage", "cost", "events", "status",
    }
    assert result.record.publication_complete is True
    assert result.record.generation_succeeded is True
    response_receipt = json.loads(store.get_bytes(result.record.archives["response"]))
    usage_receipt = json.loads(store.get_bytes(result.record.archives["usage"]))
    attempt_receipt = json.loads(store.get_bytes(result.record.archives["attempt"]))
    assert usage_receipt["accepted_response_usage"] is True
    assert usage_receipt["accepted"]["token_ids"] == [100]
    assert attempt_receipt["protocol_version"] == 3
    assert attempt_receipt["producer"] == "feature_rl.generation.LocalGenerationProvider"
    assert attempt_receipt["recorded_at"].endswith("Z")
    assert attempt_receipt["source_sha256"]["provider.py"] == hashlib.sha256(
        Path("src/feature_rl/generation/provider.py").read_bytes()
    ).hexdigest()
    assert attempt_receipt["model_config_sha256"] == (
        "574349e5a343236546fda55e4744a76e181f534182d7dc60ff1bad7e7a502849"
    )
    assert response_receipt["max_sampled_physical_footprint_bytes"] == 2_000_000_000
    assert response_receipt["max_reported_lifetime_physical_footprint_bytes"] == 2_100_000_000
    assert response_receipt["process_group_cleanup_verified"] is True
    sent = json.loads(runner.calls[0]["stdin"])
    assert sent["protocol_version"] == 3
    assert sent["response_id"] == "RESP_1"
    assert sent["max_output_tokens"] == 128
    assert "SRC_1" in sent["prompt"]
    assert "FEATURE_1" in sent["prompt"]
    child_env = runner.calls[0]["environment"]
    assert child_env.get("HOME") == os.environ.get("HOME")
    assert child_env["HF_HOME"] != os.environ.get("HF_HOME")
    assert {"HF_HUB_CACHE", "HF_XET_CACHE", "TRANSFORMERS_CACHE"} <= set(child_env)


def test_provider_validates_nested_strict_types_through_json_transport(tmp_path):
    """JSON arrays, enums and timestamps must reach strict models through JSON mode."""
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {
        "values": ["one", "two"],
        "visibility": "authoring",
        "recorded_at": "2026-09-19T10:00:00Z",
    }
    events[-1]["output_text"] = json.dumps(envelope)
    runner = FakeRunner(stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode())
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    result = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes, runner=runner
    ).generate(request(), StructuredContent)
    assert result.content.values == ("one", "two")
    assert result.content.visibility is Visibility.AUTHORING
    assert result.content.recorded_at == datetime(2026, 9, 19, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "invalid_content",
    [
        {"values": ["valid"], "visibility": "unknown", "recorded_at": "2026-09-19T10:00:00Z"},
        {"values": [1], "visibility": "authoring", "recorded_at": "2026-09-19T10:00:00Z"},
        {"values": ["valid"], "visibility": "authoring", "recorded_at": "not-a-time"},
    ],
)
def test_provider_json_transport_keeps_nested_strict_rejections(tmp_path, invalid_content):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = invalid_content
    events[-1]["output_text"] = json.dumps(envelope)
    runner = FakeRunner(stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode())
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes, runner=runner
    )
    with pytest.raises(GenerationProviderError, match="strict schema"):
        provider.generate(request(), StructuredContent)


def test_provider_preserves_exact_nested_literal_json_types(tmp_path):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"leaf": {"confirmed": True, "version": 3}}
    events[-1]["output_text"] = json.dumps(envelope)
    valid = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "valid", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()),
    ).generate(request(), NestedLiteralContent)
    assert valid.content.leaf.confirmed is True
    assert type(valid.content.leaf.version) is int

    for field, wrong_value in (("confirmed", 1), ("version", 3.0)):
        changed = [dict(item) for item in events]
        changed_envelope = json.loads(changed[-1]["output_text"])
        changed_envelope["content"]["leaf"][field] = wrong_value
        changed[-1]["output_text"] = json.dumps(changed_envelope)
        provider = LocalGenerationProvider(
            backend=FakeBackend(),
            archive=ArtifactStore(tmp_path / f"invalid-{field}", ActorRole.AUTHOR).put_bytes,
            runner=FakeRunner(
                stdout=("\n".join(json.dumps(item) for item in changed) + "\n").encode()
            ),
        )
        with pytest.raises(GenerationProviderError, match="literal JSON type"):
            provider.generate(request(), NestedLiteralContent)


@pytest.mark.parametrize(
    ("schema", "valid_values", "invalid_value"),
    [
        (
            UnionLiteralContent,
            ({"mode": {"flag": True}}, {"mode": {"flag": False}}),
            {"mode": {"flag": 1}},
        ),
        (
            PatternedLiteralContent,
            ({"flags": {"k_flag": True}},),
            {"flags": {"k_flag": 1}},
        ),
    ],
)
def test_provider_exact_literal_validation_follows_actual_schema_paths(
    tmp_path, schema, valid_values, invalid_value
):
    for index, content in enumerate(valid_values):
        events = [json.loads(line) for line in worker_events().splitlines()]
        envelope = json.loads(events[-1]["output_text"])
        envelope["content"] = content
        events[-1]["output_text"] = json.dumps(envelope)
        result = LocalGenerationProvider(
            backend=FakeBackend(),
            archive=ArtifactStore(tmp_path / f"valid-{index}", ActorRole.AUTHOR).put_bytes,
            runner=FakeRunner(
                stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
            ),
        ).generate(request(), schema)
        assert result.content.model_dump(mode="json") == content

    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = invalid_value
    events[-1]["output_text"] = json.dumps(envelope)
    provider = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "invalid", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    )
    with pytest.raises(GenerationProviderError, match="literal JSON type"):
        provider.generate(request(), schema)


def test_provider_exact_literal_validation_returns_the_branch_that_checked_raw_types(
    tmp_path,
):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"mode": {"flag": 1}}
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), MixedLiteralContent)
    assert isinstance(result.content.mode, NumberAlternative)
    assert type(result.content.mode.flag) is int
    assert result.content.model_dump_json() == '{"mode":{"flag":1}}'


@pytest.mark.parametrize(
    ("schema", "content", "field", "expected_type", "serialized"),
    [
        (ScalarBooleanUnionContent, {"flag": True}, "flag", bool, {"flag": True}),
        (ScalarBooleanUnionContent, {"flag": 1}, "flag", int, {"flag": 1}),
        (ScalarIntegerUnionContent, {"value": 3}, "value", int, {"value": 3}),
        (ScalarIntegerUnionContent, {"value": 3.0}, "value", float, {"value": 3.0}),
    ],
)
def test_provider_preserves_selected_scalar_literal_union_branch(
    tmp_path, schema, content, field, expected_type, serialized
):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = content
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), schema)
    assert type(getattr(result.content, field)) is expected_type
    assert json.loads(result.content.model_dump_json()) == serialized


def test_provider_preserves_nested_scalar_literal_union_branches(tmp_path):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"values": [1, True, 2]}
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), NestedScalarUnionContent)
    assert tuple(type(value) for value in result.content.values) == (int, bool, int)
    assert result.content.model_dump_json() == '{"values":[1,true,2]}'


def test_provider_exact_literal_validation_preserves_json_enum_transport(tmp_path):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {
        "visibility": "authoring",
        "values": ["alpha", "beta"],
        "recorded_at": "2026-09-19T10:00:00Z",
    }
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), EnumLiteralContent)
    assert result.content.visibility is Visibility.AUTHORING
    assert result.content.values == ("alpha", "beta")
    assert result.content.recorded_at == datetime(2026, 9, 19, 10, tzinfo=timezone.utc)


def test_string_literal_original_validation_runs_defaults_and_post_init_once(tmp_path):
    lifecycle = []

    class LifecycleLiteralContent(StrictModel):
        flag: Literal["ready"]
        marker: str = Field(
            default_factory=lambda: lifecycle.append("default") or "generated"
        )

        def model_post_init(self, _context):
            lifecycle.append("post_init")

    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"flag": "ready"}
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), LifecycleLiteralContent)
    assert result.content.marker == "generated"
    assert lifecycle == ["default", "post_init"]


def test_string_literal_original_validation_preserves_json_callback_mode(tmp_path):
    assert (
        JsonModeLiteralContent.model_validate_json(b'{"flag":"ready"}').flag
        == "ready"
    )
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"flag": "ready"}
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), JsonModeLiteralContent)
    assert result.content.flag == "ready"


def test_string_literal_schema_preserves_after_chain_callback(tmp_path):
    raw = {"flag": "ready", "number": "3"}
    baseline = AfterChainStringLiteralContent.model_validate_json(json.dumps(raw))
    assert baseline.number == 3
    assert type(baseline.number) is int
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), AfterChainStringLiteralContent)
    assert result.content.number == 3
    assert type(result.content.number) is int


def test_string_literal_schema_runs_omitting_callback_once(tmp_path):
    raw = {"flag": "ready", "values": ["skip", "keep"]}
    OMIT_CALLBACKS.clear()
    baseline = OmitStringLiteralContent.model_validate_json(json.dumps(raw))
    assert baseline.values == ["keep"]
    assert OMIT_CALLBACKS == ["skip", "keep"]
    OMIT_CALLBACKS.clear()
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), OmitStringLiteralContent)
    assert result.content.values == ["keep"]
    assert OMIT_CALLBACKS == ["skip", "keep"]


@pytest.mark.parametrize(
    ("schema", "raw", "expected"),
    [
        (
            AfterChainNumericLiteralContent,
            {"flag": True, "number": "3"},
            lambda value: value.number == 3,
        ),
        (
            OmitNumericLiteralContent,
            {"flag": True, "values": ["skip", "keep"]},
            lambda value: value.values == ["keep"],
        ),
    ],
)
def test_numeric_literal_callback_dependencies_refuse_before_execution(
    tmp_path, schema, raw, expected
):
    baseline = schema.model_validate_json(json.dumps(raw))
    assert expected(baseline)
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    with pytest.raises(
        GenerationProviderError, match="numeric or Boolean Literal"
    ) as caught:
        LocalGenerationProvider(
            backend=backend, archive=store.put_bytes, runner=runner
        ).generate(request(), schema)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


def _guarded_set_raw(*, nested, equal):
    labels = ("same", "same") if equal else ("first", "second")
    values = [{"flag": True, "label": label} for label in labels]
    return {"payload": {"values": values}} if nested else {"values": values}


@pytest.mark.parametrize(
    ("schema", "nested"),
    [
        (GuardedModelSetContent, False),
        (GuardedModelFrozenSetContent, False),
        (NestedGuardedModelSetContent, True),
        (NestedGuardedModelFrozenSetContent, True),
    ],
)
@pytest.mark.parametrize("equal", [False, True])
def test_numeric_literal_model_sets_refuse_before_hash_sensitive_restoration(
    tmp_path, schema, nested, equal
):
    raw = _guarded_set_raw(nested=nested, equal=equal)
    if equal:
        with pytest.raises(ValidationError, match="at least 2 items"):
            schema.model_validate_json(json.dumps(raw))
    else:
        baseline = schema.model_validate_json(json.dumps(raw))
        values = baseline.payload.values if nested else baseline.values
        assert len(values) == 2
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    with pytest.raises(
        GenerationProviderError, match="hash-sensitive"
    ) as caught:
        LocalGenerationProvider(
            backend=backend, archive=store.put_bytes, runner=runner
        ).generate(request(), schema)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


@pytest.mark.parametrize(
    ("schema", "raw"),
    [
        (NumericLiteralDefaultContent, {"flag": True}),
        (NumericKeyLiteralMappingContent, {"flags": {"1": True}}),
    ],
)
def test_other_unsupported_numeric_literal_structures_refuse_before_execution(
    tmp_path, schema, raw
):
    schema.model_validate_json(json.dumps(raw))
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    with pytest.raises(GenerationProviderError) as caught:
        LocalGenerationProvider(
            backend=backend, archive=store.put_bytes, runner=runner
        ).generate(request(), schema)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


def test_non_json_native_literal_refuses_before_execution(tmp_path):
    baseline = UnsupportedDecimalLiteralContent.model_validate_json(b'{"value":1}')
    assert baseline.value == Decimal("1")
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    with pytest.raises(
        GenerationProviderError, match="unsupported Literal value types"
    ) as caught:
        LocalGenerationProvider(
            backend=backend, archive=store.put_bytes, runner=runner
        ).generate(request(), UnsupportedDecimalLiteralContent)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


@pytest.mark.parametrize(
    ("schema", "raw", "extra"),
    [
        (
            ExtraAllowNestedContent,
            {"flag": True, "item": {"x": 1, "y": 2}},
            lambda value: value.item.__pydantic_extra__,
        ),
        (
            ExtraAllowRootContent,
            {"flag": True, "x": 1, "y": 2},
            lambda value: value.__pydantic_extra__,
        ),
    ],
)
def test_numeric_literal_extra_allow_models_refuse_before_execution(
    tmp_path, schema, raw, extra
):
    baseline = schema.model_validate_json(json.dumps(raw))
    assert extra(baseline) == {"y": 2}
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    with pytest.raises(
        GenerationProviderError, match="extra='allow'"
    ) as caught:
        LocalGenerationProvider(
            backend=backend, archive=store.put_bytes, runner=runner
        ).generate(request(), schema)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


@pytest.mark.parametrize(
    ("schema", "raw"),
    [
        (ExtraIgnoreNestedContent, {"flag": True, "item": {"x": 1, "y": 2}}),
        (ExtraForbidNestedContent, {"flag": True, "item": {"x": 1}}),
    ],
)
def test_numeric_literal_nested_models_preserve_ignore_and_forbid_extra_modes(
    tmp_path, schema, raw
):
    baseline = schema.model_validate_json(json.dumps(raw))
    assert baseline.item.x == 1
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), schema)
    assert result.content.item.x == 1
    assert result.content.item.__pydantic_extra__ is None


def test_unsupported_literal_schema_refuses_before_backend_execution(tmp_path):
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=backend, archive=store.put_bytes, runner=runner
    )
    with pytest.raises(
        GenerationProviderError, match="numeric or Boolean Literal"
    ) as caught:
        provider.generate(request(), UnsupportedPlainLiteralContent)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.success is False
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


def test_literal_union_with_branch_callback_refuses_before_backend_execution(tmp_path):
    baseline = CallbackUnionContent.model_validate_json(b'{"mode":{"flag":true}}')
    assert isinstance(baseline.mode, AllowedBooleanAlternative)
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=backend, archive=store.put_bytes, runner=runner
    )
    with pytest.raises(
        GenerationProviderError, match="numeric or Boolean Literal"
    ) as caught:
        provider.generate(request(), CallbackUnionContent)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"


@pytest.mark.parametrize(
    ("schema", "content", "accepted_type"),
    [
        (
            PostInitUnionContent,
            {"mode": {"flag": True}},
            AllowedBooleanAlternative,
        ),
        (BeforeFieldContent, {"flag": True, "number": "3"}, BeforeFieldContent),
        (WrapFieldContent, {"flag": True, "number": "3"}, WrapFieldContent),
        (
            ReferencedBeforeContent,
            {
                "flag": True,
                "first": {"number": "3"},
                "second": {"number": "4"},
            },
            ReferencedBeforeContent,
        ),
    ],
)
def test_callback_sensitive_literal_schemas_refuse_before_backend_execution(
    tmp_path, schema, content, accepted_type
):
    baseline = schema.model_validate_json(json.dumps(content))
    if schema is PostInitUnionContent:
        assert isinstance(baseline.mode, accepted_type)
    else:
        assert isinstance(baseline, accepted_type)
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=backend, archive=store.put_bytes, runner=runner
    )
    with pytest.raises(
        GenerationProviderError, match="numeric or Boolean Literal"
    ) as caught:
        provider.generate(request(), schema)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.record.error_code == "GenerationSchemaUnsupportedError"
    assert caught.value.record.archives.keys() == set(ARCHIVE_NAMES)


def test_unsupported_callback_schema_publication_replays_without_execution(tmp_path):
    backend = NeverVerifiedBackend()
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    failed = False

    def fail_schema_once(data, kind, visibility):
        nonlocal failed
        if kind == "generation-schema" and not failed:
            failed = True
            raise OSError("injected unsupported-schema publication failure")
        return store.put_bytes(data, kind, visibility)

    provider = LocalGenerationProvider(
        backend=backend, archive=fail_schema_once, runner=runner
    )
    with pytest.raises(GenerationProviderError, match="archive publication failed") as caught:
        provider.generate(request(), BeforeFieldContent)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert caught.value.recovery is not None
    assert caught.value.record.publication_complete is False

    recovered = caught.value.replay_publication(store.put_bytes)
    assert backend.verify_calls == 0
    assert runner.calls == []
    assert recovered.publication_complete is True
    assert recovered.generation_succeeded is False
    assert recovered.error_code == "GenerationSchemaUnsupportedError"
    assert set(recovered.archives) == set(ARCHIVE_NAMES)


@pytest.mark.parametrize(
    ("schema", "example_name"),
    [
        (RequirementContract, "RequirementContract"),
        (ScenarioPlan, "ScenarioPlan"),
    ],
)
def test_provider_preserves_actual_m0_schema_json_transport(
    tmp_path, schema, example_name
):
    from test_contracts_examples import examples

    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = examples()[example_name]
    events[-1]["output_text"] = json.dumps(envelope)
    result = LocalGenerationProvider(
        backend=FakeBackend(),
        archive=ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR).put_bytes,
        runner=FakeRunner(
            stdout=("\n".join(json.dumps(item) for item in events) + "\n").encode()
        ),
    ).generate(request(), schema)
    assert isinstance(result.content, schema)


@pytest.mark.parametrize(
    ("event_index", "field", "wrong_value"),
    [
        (0, "protocol_version", 3.0),
        (0, "local_files_only", 1),
        (0, "remote_code", 0),
        (3, "fresh_process", 1),
        (3, "fresh_prompt_cache", 1),
        (5, "fresh_process", 1),
        (5, "fresh_prompt_cache", 1),
    ],
)
def test_event_protocol_rejects_numeric_equivalents_for_literals(
    event_index, field, wrong_value
):
    events = [json.loads(line) for line in worker_events().splitlines()]
    events[0]["prompt_sha256"] = "a" * 64
    events[0]["worker_source_sha256"] = "b" * 64
    assert _decode_event(json.dumps(events[event_index])).event == events[event_index]["event"]
    events[event_index][field] = wrong_value
    with pytest.raises(ValueError, match="protocol v3"):
        _decode_event(json.dumps(events[event_index]))


@pytest.mark.parametrize("field", ["model_load_started", "inference_started"])
def test_input_rejection_protocol_rejects_numeric_false(field):
    rejected = {
        "protocol_version": 3,
        "request_id": "REQ_CALL_1",
        "response_id": "RESP_1",
        "prompt_id": "PROMPT_1",
        "event": "input_rejected",
        "actual_input_tokens": 381,
        "max_input_tokens": 16,
        "model_load_started": False,
        "inference_started": False,
    }
    assert _decode_event(json.dumps(rejected)).event == "input_rejected"
    rejected[field] = 0
    with pytest.raises(ValueError, match="protocol v3"):
        _decode_event(json.dumps(rejected))


@pytest.mark.parametrize(
    ("event_index", "field", "value"),
    [
        (0, "config_sha256", "0" * 64),
        (0, "tokenizer_sha256", "0" * 64),
        (0, "weights_sha256", "0" * 64),
        (0, "model_manifest_sha256", "0" * 64),
        (0, "dependency_manifest_sha256", "0" * 64),
        (0, "dependency_versions", {"mlx": "0.0.0"}),
        (0, "seed", 1),
        (0, "prompt_sha256", "0" * 64),
        (0, "worker_source_sha256", "0" * 64),
        (0, "local_files_only", False),
        (0, "remote_code", True),
        (0, "unexpected", "forbidden"),
        (1, "max_input_tokens", 2047),
        (2, "previous_cache_limit_bytes", None),
        (3, "model_id", "different/model"),
        (4, "selected_model_logprob", 0.01),
        (4, "text_fragment", 123),
        (5, "response_id", "DIFFERENT_RESPONSE"),
        (5, "inference_seconds", -0.1),
    ],
)
def test_exact_event_protocol_rejects_each_one_field_mutation(
    tmp_path, event_index, field, value
):
    """Backend, request, measurement and score evidence cannot drift silently."""
    events = [json.loads(line) for line in worker_events().splitlines()]
    events[event_index][field] = value
    stdout = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes, runner=FakeRunner(stdout=stdout)
    )
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(request(), SmokeContent)
    assert caught.value.record.success is False


@pytest.mark.parametrize(
    ("stdout", "termination", "message"),
    [
        (worker_events(event_override={"event": "tool_call", "name": "shell"}), "process_exit", "event"),
        (worker_events(truncated=True), "process_exit", "truncated"),
        (worker_events().replace(b'"selected_model_logprob": -0.25, ', b''), "process_exit", "logprob"),
        (b"not-json\n", "process_exit", "JSON"),
        (worker_events(), "memory_cap", "memory_cap"),
        (worker_events(), "monitoring_failure", "monitoring_failure"),
    ],
)
def test_provider_fails_closed_and_archives_failure(tmp_path, stdout, termination, message):
    """Protocol, truncation, logprob and process-bound violations are never accepted."""
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes,
        runner=FakeRunner(stdout=stdout, termination=termination),
    )
    with pytest.raises(GenerationProviderError, match=message) as caught:
        provider.generate(request(), SmokeContent)
    assert caught.value.record.success is False
    assert set(caught.value.record.archives) >= {"request", "response", "events", "status", "cost"}
    archived_usage = json.loads(store.get_bytes(caught.value.record.archives["usage"]))
    assert archived_usage["accepted_response_usage"] is False
    if b'"truncated": true' in stdout:
        assert archived_usage["observed"]["input_tokens"] == 41
        assert archived_usage["observed"]["input_token_ids"] == list(range(41))
        assert archived_usage["observed"]["token_ids"] == [100]
        assert archived_usage["observed"]["selected_model_logprobs"] == [-0.25]
        assert archived_usage["observed"]["behavior_logprobs"] is None
    if termination == "monitoring_failure":
        response = json.loads(store.get_bytes(caught.value.record.archives["response"]))
        assert response["monitoring_failures"] == 1
        assert response["monitor_error_type"] == "ObservationUnavailable"
        assert caught.value.response == response


def test_provider_retains_pre_inference_rejection_count_as_partial_cost(tmp_path):
    """A rejected prompt's known token count is cost evidence, never successful usage."""
    identity = json.loads(worker_events().splitlines()[0])
    rejected = {
        "protocol_version": 3, "request_id": "REQ_CALL_1", "response_id": "RESP_1",
        "prompt_id": "PROMPT_1", "event": "input_rejected",
        "actual_input_tokens": 381, "max_input_tokens": 16,
        "model_load_started": False, "inference_started": False,
    }
    stdout = (json.dumps(identity) + "\n" + json.dumps(rejected) + "\n").encode()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes,
        runner=FakeRunner(stdout=stdout, exit_status=65),
    )
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(request(generation_limits=limits(input_tokens=16)), SmokeContent)
    usage_receipt = json.loads(store.get_bytes(caught.value.record.archives["usage"]))
    cost_receipt = json.loads(store.get_bytes(caught.value.record.archives["cost"]))
    assert usage_receipt["accepted_response_usage"] is False
    assert usage_receipt["observed"]["input_tokens"] == 381
    assert usage_receipt["observed"]["input_token_ids"] is None
    assert cost_receipt["input_tokens"] == 381
    assert cost_receipt["output_tokens"] == 0
    assert cost_receipt["measurement"] == "partial"


def test_failed_envelope_never_uses_accepted_usage_shape(tmp_path):
    """Valid token events do not make an invalid response acceptable."""
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["requirement_ids"] = ["UNKNOWN_REQUIREMENT"]
    events[-1]["output_text"] = json.dumps(envelope)
    stdout = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes, runner=FakeRunner(stdout=stdout)
    )
    with pytest.raises(GenerationProviderError, match="unknown requirement") as caught:
        provider.generate(request(), SmokeContent)
    archived = json.loads(store.get_bytes(caught.value.record.archives["usage"]))
    assert archived["accepted_response_usage"] is False
    assert archived["accepted"] is None
    assert archived["observed"]["input_tokens"] == 41
    assert archived["observed"]["token_ids"] == [100]


def test_attempt_registration_failure_prevents_execution_and_is_recoverable(tmp_path):
    """An unavailable archive cannot leave an unattributed model execution."""
    runner = FakeRunner()

    def unavailable(*_args):
        raise OSError("store unavailable")

    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=unavailable, runner=runner
    )
    with pytest.raises(GenerationProviderError, match="registration failed") as caught:
        provider.generate(request(), SmokeContent)
    assert runner.calls == []
    assert caught.value.record.archives == {}
    assert caught.value.record.publication_complete is False
    assert caught.value.recovery is not None
    assert caught.value.cost.measurement == "unknown"
    with pytest.raises(GenerationProviderError, match="replay failed") as replayed:
        caught.value.replay_publication(unavailable)
    assert replayed.value.record.publication_complete is False
    assert replayed.value.recovery is not None
    assert runner.calls == []


def test_archive_failure_retains_outcome_and_replays_without_execution(tmp_path):
    """Post-execution publication can resume from bounded payloads without a new call."""
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    published = []

    def fail_response_once(data, kind, visibility):
        if kind == "generation-response" and kind not in published:
            published.append(kind)
            raise OSError("injected archive publication failure")
        ref = store.put_bytes(data, kind, visibility)
        published.append(kind)
        return ref

    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=fail_response_once, runner=runner
    )
    with pytest.raises(GenerationProviderError, match="archive publication failed") as caught:
        provider.generate(request(), SmokeContent)
    failure = caught.value
    assert len(runner.calls) == 1
    assert failure.record.generation_succeeded is True
    assert failure.record.publication_complete is False
    assert set(failure.record.archives) == {"attempt", "request"}
    assert failure.response["wall_seconds"] == 1.5
    assert failure.cost.input_tokens == 41
    assert failure.cost.output_tokens == 1
    assert failure.usage_observation["token_ids"] == [100]

    recovered_result = failure.replay_result(store.put_bytes)
    recovered = recovered_result.record
    assert len(runner.calls) == 1
    assert recovered_result.content == SmokeContent(ok=True, nonce="unit")
    assert recovered.success is True
    assert recovered.publication_complete is True
    assert set(recovered.archives) == set(ARCHIVE_NAMES)


def oversized_request(*, maximum_ids=False) -> GenerationRequest:
    updates = {"instruction": "Attributable diagnostic instruction. " * 200}
    if maximum_ids:
        updates |= {
            "request_id": "R" * 128,
            "response_id": "S" * 128,
            "prompt_id": "P" * 128,
        }
    return GenerationRequest.model_validate(
        request(generation_limits=limits(stdin_bytes=4096)).model_dump() | updates
    )


def test_oversized_preflight_is_attributable_bounded_and_never_executes(tmp_path):
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    archive_kinds = []

    def archive(data, kind, visibility):
        archive_kinds.append(kind)
        return store.put_bytes(data, kind, visibility)

    provider = LocalGenerationProvider(backend=FakeBackend(), archive=archive, runner=runner)
    with pytest.raises(GenerationProviderError, match="input byte cap") as caught:
        provider.generate(oversized_request(), SmokeContent)

    failure = caught.value
    assert runner.calls == []
    assert failure.record.success is False
    assert failure.record.generation_succeeded is False
    assert failure.record.publication_complete is True
    assert failure.record.error_code == "GenerationInputLimitError"
    assert failure.cost.measurement == "unknown"
    assert failure.recovery is None
    assert archive_kinds == [
        "generation-attempt", "generation-preflight", "generation-cost", "generation-status"
    ]
    assert set(failure.record.archives) == {"attempt", "preflight", "cost", "status"}
    attempt = json.loads(store.get_bytes(failure.record.archives["attempt"]))
    preflight = json.loads(store.get_bytes(failure.record.archives["preflight"]))
    status = json.loads(store.get_bytes(failure.record.archives["status"]))
    assert preflight["request_id"] == "REQ_CALL_1"
    assert preflight["response_id"] == "RESP_1"
    assert preflight["prompt_id"] == "PROMPT_1"
    assert preflight["stdin_cap_bytes"] == 4096
    assert preflight["observed_bytes"]["request_json"] > 4096
    assert preflight["request_sha256"] == attempt["request_sha256"]
    assert preflight["output_schema_sha256"] == attempt["output_schema_sha256"]
    assert preflight["oversized_components"]
    assert max(len(store.get_bytes(ref)) for ref in failure.record.archives.values()) < 4096
    assert status["error_type"] == "GenerationInputLimitError"


def test_oversized_preflight_publication_failure_replays_without_execution(tmp_path):
    runner = FakeRunner()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    failed = False

    def fail_preflight_once(data, kind, visibility):
        nonlocal failed
        if kind == "generation-preflight" and not failed:
            failed = True
            raise OSError("injected preflight publication failure")
        return store.put_bytes(data, kind, visibility)

    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=fail_preflight_once, runner=runner
    )
    with pytest.raises(GenerationProviderError, match="archive publication failed") as caught:
        provider.generate(oversized_request(maximum_ids=True), SmokeContent)
    assert runner.calls == []
    assert caught.value.record.archives.keys() == {"attempt"}
    assert caught.value.recovery is not None
    assert caught.value.response["oversized_components"]

    recovered = caught.value.replay_publication(store.put_bytes)
    assert runner.calls == []
    assert recovered.success is False
    assert recovered.generation_succeeded is False
    assert recovered.publication_complete is True
    assert recovered.error_code == "GenerationInputLimitError"
    assert set(recovered.archives) == {"attempt", "preflight", "cost", "status"}
    assert max(len(store.get_bytes(ref)) for ref in recovered.archives.values()) < 4096
