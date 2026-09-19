#!/usr/bin/env python3
"""Standalone, local-only MLX worker staged into a fresh directory per call."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import pathlib
import sys
import time

MODEL_ID = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
FILES = {
    "added_tokens.json": (707, "c0284b582e14987fbd3d5a2cb2bd139084371ed9acbae488829a1c900833c680"),
    "chat_template.jinja": (4040, "40c21f34cf67d8c760ef72f8ad3ae5afad514299d4b06e91dd9a8d705af7b541"),
    "config.json": (938, "574349e5a343236546fda55e4744a76e181f534182d7dc60ff1bad7e7a502849"),
    "generation_config.json": (238, "835fffe355c9438e7a25be099b3fccaa98350b83451f9fd2d99512e74f1ade48"),
    "merges.txt": (1671853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
    "model.safetensors": (2263022417, "2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f"),
    "model.safetensors.index.json": (63964, "388d811b8b7c2608dd04cce1bcb04a8bf715d19b42790894e6d3427ff429a777"),
    "special_tokens_map.json": (613, "76862e765266b85aa9459767e33cbaf13970f327a0e88d1c65846c2ddd3a1ecd"),
    "tokenizer.json": (11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    "tokenizer_config.json": (5440, "4397cc477eb6d79715ccd2000accd6b3531928f30029665832fa1b255f24d2b9"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
}
VERSIONS = {
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


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, sort_keys=True, allow_nan=False), flush=True)


def digest(path: pathlib.Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            result.update(block)
    return result.hexdigest()


def exact_request() -> dict:
    raw = sys.stdin.buffer.read(1_048_577)
    if len(raw) > 1_048_576:
        raise RuntimeError("worker stdin exceeds absolute cap")
    request = json.loads(raw)
    expected = {
        "protocol_version", "model_directory", "model_id", "revision", "response_id", "prompt",
        "max_input_tokens", "max_output_tokens", "seed", "memory",
    }
    if not isinstance(request, dict) or set(request) != expected:
        raise RuntimeError("worker request fields differ from protocol")
    if request["protocol_version"] != 2 or request["model_id"] != MODEL_ID or request["revision"] != REVISION:
        raise RuntimeError("worker request identity mismatch")
    if type(request["max_input_tokens"]) is not int or not 0 < request["max_input_tokens"] <= 262_144:
        raise RuntimeError("worker input-token cap is invalid")
    if type(request["max_output_tokens"]) is not int or not 0 < request["max_output_tokens"] <= 262_144:
        raise RuntimeError("worker output-token cap is invalid")
    if request["max_input_tokens"] + request["max_output_tokens"] > 262_144:
        raise RuntimeError("worker token envelope exceeds the model context")
    if type(request["seed"]) is not int or request["seed"] < 0:
        raise RuntimeError("worker seed is invalid")
    if not isinstance(request["prompt"], str) or not request["prompt"].strip():
        raise RuntimeError("worker prompt is empty")
    if request["memory"] != {
        "guideline_bytes": 3_758_096_384,
        "wired_limit_bytes": 3_758_096_384,
        "cache_limit_bytes": 0,
    }:
        raise RuntimeError("worker memory policy differs from the qualified policy")
    return request


def verify_runtime(request: dict) -> tuple[pathlib.Path, dict]:
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise RuntimeError("offline inference environment is required")
    model = pathlib.Path(request["model_directory"])
    if not model.is_absolute() or not model.is_dir() or model.is_symlink():
        raise RuntimeError("model directory must be an explicit regular absolute directory")
    if {path.name for path in model.iterdir()} != set(FILES):
        raise RuntimeError("model directory violates the positive file allowlist")
    for name, (size, sha256) in FILES.items():
        path = model / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size or digest(path) != sha256:
            raise RuntimeError(f"model file identity mismatch: {name}")
    config = json.loads((model / "config.json").read_text())
    if config.get("model_type") != "qwen3" or config.get("architectures") != ["Qwen3ForCausalLM"]:
        raise RuntimeError("model architecture mismatch")
    if "auto_map" in config or "model_file" in config:
        raise RuntimeError("remote or custom model code is forbidden")
    actual_versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    if actual_versions != VERSIONS:
        raise RuntimeError("installed dependency closure mismatch")
    emit(
        "identity_validated", model_id=MODEL_ID, revision=REVISION,
        config_sha256=FILES["config.json"][1], tokenizer_sha256=FILES["tokenizer.json"][1],
        weights_sha256=FILES["model.safetensors"][1], dependency_versions=actual_versions,
        local_files_only=True, remote_code=False,
    )
    return model, config


def main() -> int:
    started = time.monotonic()
    request = exact_request()
    model_dir, config = verify_runtime(request)
    from mlx_lm.utils import load_tokenizer

    tokenizer = load_tokenizer(
        model_dir,
        {"trust_remote_code": False, "local_files_only": True},
        eos_token_ids=config.get("eos_token_id"),
    )
    input_ids = list(
        tokenizer.apply_chat_template(
            [{"role": "user", "content": request["prompt"]}],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    )
    if len(input_ids) > request["max_input_tokens"]:
        emit(
            "input_rejected", actual_input_tokens=len(input_ids),
            max_input_tokens=request["max_input_tokens"], model_load_started=False,
            inference_started=False,
        )
        return 65
    emit(
        "input_accepted", actual_input_tokens=len(input_ids), input_token_ids=input_ids,
        max_input_tokens=request["max_input_tokens"],
    )

    import mlx.core as mx
    from mlx_lm.generate import generate_step
    from mlx_lm.utils import load_model

    previous_memory = mx.set_memory_limit(request["memory"]["guideline_bytes"])
    previous_cache = mx.set_cache_limit(request["memory"]["cache_limit_bytes"])
    previous_wired = mx.set_wired_limit(request["memory"]["wired_limit_bytes"])
    mx.reset_peak_memory()
    emit(
        "memory_controls_set",
        mlx_memory_guideline_bytes=request["memory"]["guideline_bytes"],
        mlx_cache_limit_bytes=request["memory"]["cache_limit_bytes"],
        mlx_wired_limit_bytes=request["memory"]["wired_limit_bytes"],
        previous_memory_limit_bytes=previous_memory,
        previous_cache_limit_bytes=previous_cache,
        previous_wired_limit_bytes=previous_wired,
    )
    model, loaded_config = load_model(model_dir, lazy=False)
    model.eval()
    if loaded_config.get("model_type") != "qwen3":
        raise RuntimeError("loaded model identity mismatch")
    emit(
        "model_loaded", fresh_process=True, fresh_prompt_cache=True,
        active_memory_bytes=mx.get_active_memory(), peak_memory_bytes=mx.get_peak_memory(),
        cache_memory_bytes=mx.get_cache_memory(),
    )
    mx.random.seed(request["seed"])
    generated: list[int] = []
    eos_ids = set(tokenizer.eos_token_ids)
    finish_reason = "length"
    inference_started = time.monotonic()
    for position, (token, logprobs) in enumerate(
        generate_step(
            mx.array(input_ids), model, max_tokens=request["max_output_tokens"],
            prefill_step_size=min(512, request["max_input_tokens"]),
        ),
        start=1,
    ):
        token_id = int(token)
        selected_model_logprob = float(logprobs[token_id].item())
        if not math.isfinite(selected_model_logprob):
            raise RuntimeError("selected model logprob is nonfinite")
        generated.append(token_id)
        emit(
            "token", position=position, token_id=token_id,
            selected_model_logprob=selected_model_logprob,
            text_fragment=tokenizer.decode([token_id], skip_special_tokens=False),
        )
        if token_id in eos_ids:
            finish_reason = "stop"
            break
    if len(generated) > request["max_output_tokens"]:
        raise RuntimeError("emitted output token cap violated")
    truncated = finish_reason == "length" and len(generated) == request["max_output_tokens"]
    emit(
        "completed", model_id=MODEL_ID, revision=REVISION,
        response_id=request["response_id"], input_tokens=len(input_ids),
        output_tokens=len(generated), max_output_tokens=request["max_output_tokens"],
        finish_reason=finish_reason, truncated=truncated,
        sampling_policy="greedy_argmax",
        output_text=tokenizer.decode(generated, skip_special_tokens=True),
        inference_seconds=time.monotonic() - inference_started,
        total_seconds=time.monotonic() - started,
        active_memory_bytes=mx.get_active_memory(), peak_memory_bytes=mx.get_peak_memory(),
        cache_memory_bytes=mx.get_cache_memory(), fresh_process=True, fresh_prompt_cache=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
