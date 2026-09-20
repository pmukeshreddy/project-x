#!/usr/bin/env python3
"""Offline Transformers worker, staged in a fresh directory for each call."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
import time

PROTOCOL_VERSION = 4


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            result.update(block)
    return result.hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def checked_manifest(path: Path, expected: str) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or digest(path) != expected:
        raise ValueError("manifest identity differs from its configured SHA-256")
    result = json.loads(path.read_text(), object_pairs_hook=_pairs)
    if not isinstance(result, dict):
        raise ValueError("manifest must be an object")
    return result


def dependency_versions(manifest, installed=None) -> dict[str, str]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("wheels"), list):
        raise ValueError("dependency manifest must contain pinned wheels")
    versions = {}
    for record in manifest["wheels"]:
        if not isinstance(record, dict):
            raise ValueError("invalid dependency record")
        name, version = record.get("name"), record.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not version:
            raise ValueError("dependency name and version required")
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in versions or not record.get("filename", "").endswith(".whl"):
            raise ValueError("dependency closure must contain distinct pinned wheels")
        if not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")):
            raise ValueError("dependency wheel SHA-256 required")
        versions[name] = version
    if not {"torch", "transformers", "tokenizers", "safetensors", "packaging"} <= versions.keys():
        raise ValueError("dependency closure must include Torch, Transformers, tokenizers, safetensors and packaging")
    try:
        actual = installed if installed is not None else {
            name: importlib.metadata.version(name) for name in versions
        }
    except importlib.metadata.PackageNotFoundError as error:
        raise ValueError(f"missing pinned dependency: {error}") from error
    for name, version in versions.items():
        if actual.get(name) != version:
            raise ValueError(f"installed dependency {name} must be exactly {version}")
    if installed is None:
        from packaging.requirements import Requirement
        for name in versions:
            for text in importlib.metadata.requires(name) or ():
                requirement = Requirement(text)
                if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                    continue
                dependency = re.sub(r"[-_.]+", "-", requirement.name).lower()
                if (requirement.url or dependency not in versions
                        or not requirement.specifier.contains(versions[dependency], prereleases=True)):
                    raise ValueError(f"dependency manifest omits or conflicts with {name}: {text}")
    return versions


def model_files(manifest, model: Path) -> dict:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("model_id"), str):
        raise ValueError("model manifest must name a model")
    if not re.fullmatch(r"[0-9a-f]{40,64}", manifest.get("revision", "")):
        raise ValueError("model revision must be an immutable commit hash")
    if model.is_symlink() or not model.is_dir():
        raise ValueError("model directory must be a regular directory")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("model file manifest required")
    files = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid model file record")
        name = record.get("path")
        if (not isinstance(name, str) or Path(name).name != name or name in {".", ".."}
                or name in files or not name.endswith((".json", ".txt", ".model", ".jinja", ".safetensors"))):
            raise ValueError("model files must be distinct inert files in one directory")
        path = model / name
        if (path.is_symlink() or not path.is_file() or type(record.get("bytes")) is not int
                or path.stat().st_size != record["bytes"] or digest(path) != record.get("sha256")):
            raise ValueError(f"model file identity mismatch: {name}")
        files[name] = record
    if ({path.name for path in model.iterdir()} != files.keys()
            or set(manifest.get("runtime_positive_allowlist", ())) != files.keys()):
        raise ValueError("model directory differs from the pinned positive allowlist")
    if not {"config.json", "tokenizer.json", "tokenizer_config.json"} <= files.keys():
        raise ValueError("local model and fast-tokenizer configs required")
    total = sum(record["bytes"] for record in files.values())
    if total != manifest.get("actual_total_bytes"):
        raise ValueError("model total bytes differ from manifest")
    for name in ("config.json", "tokenizer_config.json"):
        config = json.loads((model / name).read_text(), object_pairs_hook=_pairs)
        if not isinstance(config, dict) or any(key in config for key in (
            "auto_map", "model_file", "quantization_config", "quantization",
        )):
            raise ValueError("custom code and quantized checkpoints are unsupported")
    weights = {name for name in files if name.endswith(".safetensors")}
    if "model.safetensors" in weights:
        if weights != {"model.safetensors"} or "model.safetensors.index.json" in files:
            raise ValueError("ambiguous model weight files")
    elif "model.safetensors.index.json" in files:
        index = json.loads((model / "model.safetensors.index.json").read_text(), object_pairs_hook=_pairs)
        mapping = index.get("weight_map")
        if not isinstance(mapping, dict) or not weights or set(mapping.values()) != weights:
            raise ValueError("weight index must name exactly the pinned safetensors shards")
    else:
        raise ValueError("standard Transformers safetensors weights required")
    # Single-file identity stays its file hash; sharded identity binds names and hashes.
    weights_hash = (files["model.safetensors"]["sha256"] if "model.safetensors" in weights else
        hashlib.sha256(json.dumps(
            {name: files[name]["sha256"] for name in sorted(weights)},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest())
    return dict(model_id=manifest["model_id"], revision=manifest["revision"],
                file_count=len(files), total_bytes=total,
                config_sha256=files["config.json"]["sha256"],
                tokenizer_sha256=files["tokenizer.json"]["sha256"], weights_sha256=weights_hash)


def emit(request: dict, event: str, **values) -> None:
    print(json.dumps(dict(event=event, protocol_version=PROTOCOL_VERSION,
        request_id=request["request_id"], response_id=request["response_id"],
        prompt_id=request["prompt_id"], **values), sort_keys=True, allow_nan=False), flush=True)


def exact_request() -> dict:
    raw = sys.stdin.buffer.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("worker stdin exceeds absolute cap")
    request = json.loads(raw, object_pairs_hook=_pairs)
    expected = {
        "protocol_version", "model_directory", "model_id", "revision", "response_id", "prompt",
        "request_id", "prompt_id", "max_input_tokens", "max_output_tokens", "seed", "cuda_memory_bytes",
        "model_manifest", "dependency_manifest", "model_manifest_sha256", "dependency_manifest_sha256",
        "device", "dtype",
    }
    if not isinstance(request, dict) or set(request) != expected or request["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("worker request differs from protocol")
    for name in ("max_input_tokens", "max_output_tokens"):
        if type(request[name]) is not int or not 0 < request[name] <= 262_144:
            raise ValueError("invalid token cap")
    if type(request["seed"]) is not int or request["seed"] < 0:
        raise ValueError("invalid seed")
    if not isinstance(request["prompt"], str) or not request["prompt"].strip():
        raise ValueError("empty prompt")
    if (not re.fullmatch(r"cpu|cuda:[0-9]+", request["device"])
            or request["dtype"] not in {"float32", "float16", "bfloat16"}):
        raise ValueError("unsupported explicit device or dtype")
    if request["device"] == "cpu":
        if request["cuda_memory_bytes"] is not None:
            raise ValueError("CPU authoring must not declare a CUDA memory limit")
    elif type(request["cuda_memory_bytes"]) is not int or request["cuda_memory_bytes"] <= 0:
        raise ValueError("CUDA authoring requires an explicit positive allocator limit")
    return request


def main() -> int:
    started = time.monotonic()
    request = exact_request()
    offline = {name: os.environ.get(name) for name in (
        "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE",
    )}
    if any(value != "1" for value in offline.values()):
        raise ValueError("offline inference environment required")
    model_dir = Path(request["model_directory"])
    if not model_dir.is_absolute():
        raise ValueError("model directory must be an explicit absolute path")
    manifest = checked_manifest(Path(request["model_manifest"]), request["model_manifest_sha256"])
    identity = model_files(manifest, model_dir)
    if (identity["model_id"], identity["revision"]) != (request["model_id"], request["revision"]):
        raise ValueError("worker model identity mismatch")
    versions = dependency_versions(checked_manifest(
        Path(request["dependency_manifest"]), request["dependency_manifest_sha256"],
    ))
    emit(request, "identity_validated", **{name: identity[name] for name in (
        "model_id", "revision", "config_sha256", "tokenizer_sha256", "weights_sha256",
    )}, dependency_versions=versions, model_manifest_sha256=request["model_manifest_sha256"],
        dependency_manifest_sha256=request["dependency_manifest_sha256"], seed=request["seed"],
        prompt_sha256=hashlib.sha256(request["prompt"].encode()).hexdigest(),
        worker_source_sha256=digest(Path(__file__)), offline_environment=offline,
        local_files_only=True, remote_code=False, device=request["device"], dtype=request["dtype"])

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

    torch.set_num_threads(1)
    torch.manual_seed(request["seed"])
    torch.use_deterministic_algorithms(True)
    device = torch.device(request["device"])
    if device.type == "cuda":
        if not torch.cuda.is_available() or device.index >= torch.cuda.device_count():
            raise ValueError("configured CUDA device is unavailable; no CPU fallback")
        torch.cuda.set_device(device)
        total = torch.cuda.get_device_properties(device).total_memory
        if request["cuda_memory_bytes"] > total:
            raise ValueError("configured CUDA allocator limit exceeds device memory")
        torch.cuda.set_per_process_memory_fraction(request["cuda_memory_bytes"] / total, device)
        torch.cuda.reset_peak_memory_stats(device)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False, use_fast=True)
    input_ids = list(tokenizer.apply_chat_template(
        [{"role": "user", "content": request["prompt"]}], tokenize=True,
        add_generation_prompt=True, enable_thinking=False,
    ))
    config = json.loads((model_dir / "config.json").read_text())
    context = config.get("max_position_embeddings")
    if type(context) is not int or context <= 0 or len(input_ids) + request["max_output_tokens"] > context:
        raise ValueError("declared generation exceeds the model context")
    if not input_ids or len(input_ids) > request["max_input_tokens"]:
        emit(request, "input_rejected", actual_input_tokens=len(input_ids),
             max_input_tokens=request["max_input_tokens"], model_load_started=False, inference_started=False)
        return 65
    emit(request, "input_accepted", actual_input_tokens=len(input_ids), input_token_ids=input_ids,
         max_input_tokens=request["max_input_tokens"])
    emit(request, "memory_controls_set", device=request["device"], cuda_memory_bytes=request["cuda_memory_bytes"])
    model, loading = AutoModelForCausalLM.from_pretrained(
        model_dir, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        dtype=getattr(torch, request["dtype"]), attn_implementation="eager", output_loading_info=True,
    )
    if any(loading.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        raise ValueError("checkpoint did not load exactly; initialized or unused weights are forbidden")
    model = model.to(device).eval()
    # Avoid materializing a vocabulary-sized logit row for every prefill token
    # on models exposing Transformers' last-token optimization.
    forward_options = {"logits_to_keep": 1} if "logits_to_keep" in inspect.signature(model.forward).parameters else {}

    def memory():
        return dict(
            active_memory_bytes=torch.cuda.memory_allocated(device) if device.type == "cuda" else None,
            peak_memory_bytes=torch.cuda.max_memory_allocated(device) if device.type == "cuda" else None,
            cache_memory_bytes=torch.cuda.memory_reserved(device) if device.type == "cuda" else None,
        )

    emit(request, "model_loaded", model_id=identity["model_id"], revision=identity["revision"],
         fresh_process=True, fresh_prompt_cache=True, **memory())
    eos = model.generation_config.eos_token_id
    eos_ids = {eos} if type(eos) is int else set(eos or ())
    if not eos_ids:
        raise ValueError("model must define an EOS token")
    generated, cache = [], None
    tokens = torch.tensor([input_ids], dtype=torch.long, device=device)
    inference_started = time.monotonic()
    finish_reason = "length"
    with torch.inference_mode():
        for position in range(1, request["max_output_tokens"] + 1):
            output = model(input_ids=tokens, past_key_values=cache, use_cache=True, **forward_options)
            logprobs = output.logits[0, -1].float().log_softmax(-1)
            token_id = int(logprobs.argmax().item())
            logprob = float(logprobs[token_id].item())
            if not math.isfinite(logprob):
                raise ValueError("nonfinite selected token logprob")
            generated.append(token_id)
            emit(request, "token", position=position, token_id=token_id,
                 selected_model_logprob=logprob,
                 text_fragment=tokenizer.decode([token_id], skip_special_tokens=False))
            if token_id in eos_ids:
                finish_reason = "stop"
                break
            cache = output.past_key_values
            if cache is None:
                raise ValueError("causal model did not return a KV cache")
            tokens = torch.tensor([[token_id]], dtype=torch.long, device=device)
            del output, logprobs
    emit(request, "completed", model_id=identity["model_id"], revision=identity["revision"],
         input_tokens=len(input_ids), output_tokens=len(generated), max_output_tokens=request["max_output_tokens"],
         finish_reason=finish_reason, sampling_policy="greedy_argmax", truncated=finish_reason == "length",
         output_text=tokenizer.decode(generated, skip_special_tokens=True),
         inference_seconds=time.monotonic() - inference_started, total_seconds=time.monotonic() - started,
         fresh_process=True, fresh_prompt_cache=True, **memory())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
