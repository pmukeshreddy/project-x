#!/usr/bin/env python3
"""Run the two authorized production-boundary checks and print one JSON receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef, StrictModel, Visibility
from feature_rl.generation import (
    AuthoringContext,
    BackendConfig,
    GenerationLimits,
    GenerationProviderError,
    GenerationRequest,
    GenerationStage,
    LocalGenerationProvider,
)

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = ROOT / ".feature-rl" / "research" / "M2"
MODEL = RESEARCH / "model" / "mlx-community--Qwen3-4B-Instruct-2507-4bit" / "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"


class SmokeContent(StrictModel):
    status: Literal["BLUE"]
    nonce: Literal["M2-PROD-1"]


def limits(mode: str) -> GenerationLimits:
    return GenerationLimits(
        measurement_profile="tiny_smoke_2048x128",
        wall_seconds=120.0,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        file_size_bytes=1_048_576,
        input_tokens=16 if mode == "reject-input" else 2048,
        output_tokens=16 if mode == "reject-input" else 128,
        mlx_memory_guideline_bytes=3_758_096_384,
        mlx_wired_limit_bytes=3_758_096_384,
        mlx_cache_limit_bytes=0,
        physical_footprint_kill_bytes=4_294_967_296,
        physical_footprint_poll_seconds=0.02,
        declared_memory_ceiling_bytes=5_368_709_120,
    )


def make_request(mode: str) -> GenerationRequest:
    text = "Synthetic provider boundary check. Return BLUE and nonce M2-PROD-1."
    digest = hashlib.sha256(text.encode()).hexdigest()
    return GenerationRequest(
        request_id="M2_PROD_INPUT_REJECTION" if mode == "reject-input" else "M2_PROD_SMOKE_1",
        response_id="M2_PROD_REJECT_RESPONSE" if mode == "reject-input" else "M2_PROD_RESPONSE_1",
        prompt_id="M2_PROD_REJECT_PROMPT" if mode == "reject-input" else "M2_PROD_PROMPT_1",
        stage=GenerationStage.INITIAL_AUTHORING,
        system_prompt="Emit one JSON object only. Copy all required identifiers exactly.",
        instruction="Return status BLUE and nonce M2-PROD-1 in the required envelope.",
        contexts=(
            AuthoringContext(
                context_id="M2_SMOKE_SOURCE_1",
                role="request",
                source=ArtifactRef(
                    sha256=digest,
                    kind="request-snapshot",
                    schema_version=1,
                    visibility=Visibility.AUTHORING,
                    encoding="bytes",
                ),
                locator="synthetic.smoke",
                text=text,
                provenance_label="historical_request",
            ),
        ),
        allowed_requirement_ids=("M2_SMOKE_REQUIREMENT_1",),
        limits=limits(mode),
        seed=0,
    )


def decoded_archives(store: ArtifactStore, record) -> dict:
    result = {}
    for name, ref in record.archives.items():
        data = store.get_bytes(ref)
        if name == "events":
            result[name] = [json.loads(line) for line in data.decode().splitlines()]
        else:
            result[name] = json.loads(data)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("reject-input", "smoke"))
    args = parser.parse_args()
    store = ArtifactStore(RESEARCH / "provider-production-store", ActorRole.AUTHOR)
    provider = LocalGenerationProvider(
        backend=BackendConfig(
            python_executable=Path(sys.executable),
            model_directory=MODEL,
            model_manifest=RESEARCH / "model-acquisition.json",
            dependency_manifest=ROOT / "docs" / "evidence" / "M2" / "local-mlx-dependencies.json",
        ),
        archive=store.put_bytes,
    )
    try:
        result = provider.generate(make_request(args.mode), SmokeContent)
    except GenerationProviderError as error:
        receipt = {
            "mode": args.mode,
            "expected_rejection": args.mode == "reject-input",
            "provider_error": str(error),
            "record": error.record.model_dump(mode="json"),
            "archives": decoded_archives(store, error.record),
        }
        print(json.dumps(receipt, indent=2, sort_keys=True))
        if args.mode != "reject-input":
            return 1
        events = receipt["archives"]["events"]
        names = [event.get("event") for event in events]
        return 0 if "input_rejected" in names and "model_loaded" not in names else 2
    receipt = {
        "mode": args.mode,
        "content": result.content.model_dump(mode="json"),
        "usage": result.usage.model_dump(mode="json"),
        "cost": result.cost.model_dump(mode="json"),
        "record": result.record.model_dump(mode="json"),
        "archives": decoded_archives(store, result.record),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if args.mode == "smoke" else 3


if __name__ == "__main__":
    raise SystemExit(main())
