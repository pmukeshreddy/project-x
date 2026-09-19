#!/usr/bin/env python3
"""One explicitly budgeted native integration check; never retries inference.

Run only after the provider owner fixes and independent review pass. This is
synthetic engineering evidence, not feature construction or training evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, StrictModel, Visibility
from feature_rl.generation import (
    AuthoringContext,
    BackendConfig,
    BoundedProcessRunner,
    GenerationLimits,
    GenerationProviderError,
    GenerationRequest,
    GenerationStage,
    LocalGenerationProvider,
)


ROOT = Path(__file__).resolve().parents[3]
RESEARCH = ROOT / ".feature-rl/research/M2"
MODEL_REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"


class Colour(str, Enum):
    BLUE = "BLUE"


class NativeContent(StrictModel):
    status: Colour
    tags: tuple[Literal["local"], Literal["observed"]]
    nonce: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, timeout=15
    ).stdout


class ObservedNativeRunner(BoundedProcessRunner):
    """Observe the real native runner's inputs without modifying them."""

    def __init__(self):
        super().__init__()
        self.observations = None
        self.calls = 0

    def run(self, **arguments):
        self.calls += 1
        if self.calls != 1:
            raise RuntimeError("coordinator native verification permits one worker call")
        environment = arguments["environment"]
        self.observations = {
            "home_preserved": environment.get("HOME") == os.environ.get("HOME"),
            "home_presence_preserved": ("HOME" in environment) == ("HOME" in os.environ),
            "environment_names": sorted(environment),
            "working_directory": str(arguments["cwd"]),
            "isolated_python": "-I" in arguments["command"],
            "task_cache_paths": {
                key: environment[key]
                for key in ("TMPDIR", "HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "TRANSFORMERS_CACHE")
            },
        }
        return super().run(**arguments)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-revision", required=True)
    parser.add_argument("--review-report", type=Path, required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    product_revision = git("rev-parse", args.product_revision).decode().strip()
    report = args.review_report.resolve()
    if not report.is_relative_to(ROOT / "docs/reviews"):
        raise ValueError("review report must be a retained project review")
    review_bytes = report.read_bytes()
    source_files = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    source_bindings = {}
    for path in source_files:
        relative = path.relative_to(ROOT).as_posix()
        actual = path.read_bytes()
        committed = git("show", f"{product_revision}:{relative}")
        if actual != committed:
            raise RuntimeError(f"working source differs from reviewed product revision: {relative}")
        source_bindings[relative] = digest(actual)
    if not source_bindings:
        raise RuntimeError("no provider source was found")

    # This marker is deliberately permanent. Re-running after any failure needs
    # a separately declared new check; neither this driver nor provider retries.
    claim_path = RESEARCH / "coordinator-v3-native-claim.json"
    nonce = "C3-" + uuid.uuid4().hex[:8]
    claim = {
        "scope": "synthetic nontraining native provider integration",
        "started_at": started_at.isoformat(),
        "product_revision": product_revision,
        "coordinator_revision": git("rev-parse", "HEAD").decode().strip(),
        "review_report": report.relative_to(ROOT).as_posix(),
        "review_sha256": digest(review_bytes),
        "driver_sha256": digest(Path(__file__).read_bytes()),
        "source_sha256": source_bindings,
        "nonce": nonce,
        "max_worker_calls": 1,
        "max_input_tokens": 2048,
        "max_emitted_tokens": 128,
        "max_wall_seconds": 120,
        "max_cpu_seconds": 120,
        "max_stdin_stdout_stderr_file_bytes": 1_048_576,
        "memory_policy": "3.5GiB MLX guideline/wired; cache0; sampled4GiB physical-footprint kill at20ms with1GiB guard; not a zero-transient5GiB proof",
    }
    with claim_path.open("x") as stream:
        json.dump(claim, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    store = ArtifactStore(RESEARCH / "coordinator-v3-store", ActorRole.AUTHOR)
    source_text = (
        "Synthetic coordinator engineering source. Return status BLUE, tags "
        f'["local","observed"], and nonce {nonce}. '
        "This is not a historical feature request or training trajectory."
    )
    source = store.put_bytes(source_text.encode(), "request-snapshot", Visibility.AUTHORING)
    if store.get_bytes(source) != source_text.encode():
        raise RuntimeError("published synthetic context does not resolve exactly")
    limits = GenerationLimits(
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
    request = GenerationRequest(
        request_id="M2_C3_REQUEST",
        response_id="M2_C3_RESPONSE",
        prompt_id="M2_C3_PROMPT",
        stage=GenerationStage.INITIAL_AUTHORING,
        system_prompt="Return exactly one JSON object. Copy all required identifiers exactly.",
        instruction=f'Return status BLUE, tags ["local","observed"], and nonce {nonce} in the required envelope.',
        contexts=(AuthoringContext(
            context_id="M2_C3_SOURCE",
            role="request",
            source=source,
            locator="utf8:whole-document",
            text=source_text,
            provenance_label="reconstructed_specification",
        ),),
        allowed_requirement_ids=("M2_C3_CHECK",),
        limits=limits,
        seed=0,
    )
    runner = ObservedNativeRunner()
    provider = LocalGenerationProvider(
        backend=BackendConfig(
            python_executable=Path(sys.executable),
            model_directory=RESEARCH / "model/mlx-community--Qwen3-4B-Instruct-2507-4bit" / MODEL_REVISION,
            model_manifest=RESEARCH / "model-acquisition.json",
            dependency_manifest=ROOT / "docs/evidence/M2/local-mlx-dependencies.json",
        ),
        archive=store.put_bytes,
        runner=runner,
    )
    receipt = {"claim": claim, "source": source.model_dump(mode="json"), "checks": {}}
    result = None
    try:
        result = provider.generate(request, NativeContent)
        record = result.record
        receipt.update({
            "content": result.content.model_dump(mode="json"),
            "usage": result.usage.model_dump(mode="json"),
            "cost": result.cost.model_dump(mode="json"),
        })
    except GenerationProviderError as error:
        record = error.record
        receipt.update({
            "provider_error_type": type(error).__name__,
            "provider_error": str(error),
            "known_cost": error.cost.model_dump(mode="json") if error.cost else None,
            "response": error.response,
            "usage_observation": error.usage_observation,
            "publication_recovery_available": error.recovery is not None,
        })
    receipt["record"] = record.model_dump(mode="json")
    archives = {}
    for name, ref in record.archives.items():
        data = store.get_bytes(ref)
        try:
            archives[name] = (
                [json.loads(line) for line in data.splitlines()]
                if name == "events" else json.loads(data)
            )
        except (ValueError, UnicodeError) as error:
            archives[name] = {
                "coordinator_decode_error": str(error),
                "raw_base64": base64.b64encode(data).decode(),
            }
    receipt["archives"] = archives
    receipt["runner_observations"] = runner.observations
    checks = receipt["checks"]
    checks["one_worker_call"] = runner.calls == 1
    checks["generation_and_publication_succeeded"] = (
        record.success and record.generation_succeeded and record.publication_complete
    )
    if result is not None:
        checks.update({
            "fresh_nonce_exact": result.content.nonce == nonce,
            "strict_enum_preserved": result.content.status is Colour.BLUE,
            "strict_tuple_preserved": result.content.tags == ("local", "observed") and isinstance(result.content.tags, tuple),
            "native_input_cap": result.usage.input_tokens <= 2048,
            "native_output_cap": result.usage.output_tokens <= 128,
            "exact_input_ids": len(result.usage.input_token_ids) == result.usage.input_tokens,
            "exact_output_ids_scores": len(result.usage.token_ids) == len(result.usage.selected_model_logprobs) == result.usage.output_tokens,
            "scores_nonpositive": all(score <= 0 for score in result.usage.selected_model_logprobs),
            "explicit_nontraining_behavior": result.usage.sampling_policy == "greedy_argmax" and result.usage.behavior_logprobs is None,
            "not_truncated": not result.usage.truncated and result.usage.finish_reason == "stop",
            "measured_cpu_wall": result.cost.cpu_seconds is not None and result.cost.wall_seconds is not None,
            "unknown_money_gpu_human": result.cost.usd is None and result.cost.gpu_seconds is None and result.cost.human_minutes is None,
        })
    if runner.observations is not None:
        checks.update({
            "home_preserved": runner.observations["home_preserved"] and runner.observations["home_presence_preserved"],
            "isolated_python": runner.observations["isolated_python"],
            "temporary_workspace_removed": not Path(runner.observations["working_directory"]).exists(),
            "temporary_caches_removed": all(not Path(path).exists() for path in runner.observations["task_cache_paths"].values()),
        })
    response = archives.get("response", {})
    checks.update({
        "process_cleanup_verified": response.get("process_group_cleanup_verified") is True,
        "memory_observed": (response.get("memory_samples") or 0) > 0,
        "no_observation_failure": response.get("monitoring_failures") == 0,
        "native_exit_zero": response.get("exit_status") == 0 and response.get("termination") == "process_exit",
        "usage_archive_accepted": archives.get("usage", {}).get("accepted_response_usage") is True,
        "all_archives_resolved": set(archives) == {"attempt", "request", "response", "retrieval", "schema", "options", "provenance", "usage", "cost", "events", "status"},
        "attempt_source_binding": archives.get("attempt", {}).get("source_sha256") == {
            Path(path).name: value for path, value in source_bindings.items()
        },
    })
    receipt["ended_at"] = datetime.now(timezone.utc).isoformat()
    receipt["coordinator_wall_seconds"] = time.monotonic() - started
    receipt["passed"] = bool(checks) and all(checks.values())
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
