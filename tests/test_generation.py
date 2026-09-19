"""Risk-based tests for the configured local generation provider."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef, StrictModel, Visibility
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


def request(*, stage=GenerationStage.INITIAL_AUTHORING, contexts=None) -> GenerationRequest:
    return GenerationRequest(
        request_id="REQ_CALL_1",
        response_id="RESP_1",
        prompt_id="PROMPT_1",
        stage=stage,
        system_prompt="Return only the required JSON envelope.",
        instruction="Extract the requested structured data.",
        contexts=tuple(contexts or (context(),)),
        allowed_requirement_ids=("FEATURE_1",),
        limits=limits(),
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


class SmokeContent(StrictModel):
    ok: bool
    nonce: str


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
                tokenizer_sha256="aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
                weights_sha256="2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f",
            ),
            dependencies=VerifiedDependencies(package_count=34, versions={"mlx": "0.32.2"}),
            model_manifest_sha256="697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0",
            dependency_manifest_sha256="d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831",
        )


def worker_events(*, event_override=None, truncated=False):
    events = [
        {"event": "identity_validated", "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit", "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"},
        {"event": "input_accepted", "actual_input_tokens": 41, "input_token_ids": list(range(41)), "max_input_tokens": 2048},
        {"event": "memory_controls_set", "mlx_memory_guideline_bytes": 3_758_096_384, "mlx_cache_limit_bytes": 0, "mlx_wired_limit_bytes": 3_758_096_384},
        {"event": "model_loaded", "fresh_process": True, "fresh_prompt_cache": True},
        {"event": "token", "position": 1, "token_id": 100, "selected_model_logprob": -0.25, "text_fragment": "{"},
        {"event": "completed", "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit", "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b", "input_tokens": 41, "output_tokens": 1, "max_output_tokens": 128, "finish_reason": "stop", "sampling_policy": "greedy_argmax", "truncated": truncated, "output_text": json.dumps({"response_id":"RESP_1","source_ids":["SRC_1"],"requirement_ids":["FEATURE_1"],"content":{"ok":True,"nonce":"unit"}}), "fresh_process": True, "fresh_prompt_cache": True},
    ]
    if event_override is not None:
        events.insert(-1, event_override)
    return ("\n".join(json.dumps(item) for item in events) + "\n").encode()


class FakeRunner:
    def __init__(self, stdout=None, termination="process_exit", exit_status=0):
        self.stdout = stdout or worker_events()
        self.termination = termination
        self.exit_status = exit_status
        self.calls = []

    def run(self, **values):
        self.calls.append(values)
        return ProcessOutcome(
            termination=self.termination,
            exit_status=self.exit_status,
            wall_seconds=1.5,
            cpu_seconds=1.0,
            stdout=self.stdout,
            stderr=b"",
            memory_samples=5,
            max_sampled_physical_footprint_bytes=2_000_000_000,
            max_reported_lifetime_physical_footprint_bytes=2_100_000_000,
            breach_sample=None,
            process_group_cleanup_verified=True,
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
        "request", "response", "retrieval", "schema", "options", "provenance",
        "usage", "cost", "events", "status",
    }
    response_receipt = json.loads(store.get_bytes(result.record.archives["response"]))
    assert response_receipt["max_sampled_physical_footprint_bytes"] == 2_000_000_000
    assert response_receipt["max_reported_lifetime_physical_footprint_bytes"] == 2_100_000_000
    assert response_receipt["process_group_cleanup_verified"] is True
    sent = json.loads(runner.calls[0]["stdin"])
    assert sent["protocol_version"] == 2
    assert sent["response_id"] == "RESP_1"
    assert sent["max_output_tokens"] == 128
    assert "SRC_1" in sent["prompt"]
    assert "FEATURE_1" in sent["prompt"]
    child_env = runner.calls[0]["environment"]
    assert child_env.get("HOME") == os.environ.get("HOME")
    assert child_env["HF_HOME"] != os.environ.get("HF_HOME")
    assert {"HF_HUB_CACHE", "HF_XET_CACHE", "TRANSFORMERS_CACHE"} <= set(child_env)


@pytest.mark.parametrize(
    ("stdout", "termination", "message"),
    [
        (worker_events(event_override={"event": "tool_call", "name": "shell"}), "process_exit", "event"),
        (worker_events(truncated=True), "process_exit", "truncated"),
        (worker_events().replace(b'"selected_model_logprob": -0.25, ', b''), "process_exit", "logprob"),
        (b"not-json\n", "process_exit", "JSON"),
        (worker_events(), "memory_cap", "memory_cap"),
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


def test_provider_retains_pre_inference_rejection_count_as_partial_cost(tmp_path):
    """A rejected prompt's known token count is cost evidence, never successful usage."""
    stdout = (
        json.dumps({
            "event": "identity_validated",
            "model_id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
            "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
        })
        + "\n"
        + json.dumps({
            "event": "input_rejected", "actual_input_tokens": 381,
            "max_input_tokens": 16, "model_load_started": False,
            "inference_started": False,
        })
        + "\n"
    ).encode()
    store = ArtifactStore(tmp_path / "objects", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=FakeBackend(), archive=store.put_bytes,
        runner=FakeRunner(stdout=stdout, exit_status=65),
    )
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(request(), SmokeContent)
    usage_receipt = json.loads(store.get_bytes(caught.value.record.archives["usage"]))
    cost_receipt = json.loads(store.get_bytes(caught.value.record.archives["cost"]))
    assert usage_receipt["accepted_response_usage"] is False
    assert usage_receipt["observed"]["input_tokens"] == 381
    assert usage_receipt["observed"]["input_token_ids"] is None
    assert cost_receipt["input_tokens"] == 381
    assert cost_receipt["output_tokens"] == 0
    assert cost_receipt["measurement"] == "partial"
