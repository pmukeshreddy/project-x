"""Read-only reconciliation of the pinned local authoring qualification.

This does not load model weights for inference, rerun a candidate, or establish
production-provider correctness. Run from the repository root using .venv.
"""

import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone


ROOT = Path.cwd()
OUT = ROOT / "docs/evidence/M2/coordinator-local-mlx-verification.json"
checks = {}
started = time.monotonic()
receipt = {
    "producer": "/root coordinator",
    "utc": datetime.now(timezone.utc).isoformat(),
    "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "command": [sys.executable, str(Path(__file__).relative_to(ROOT))],
    "purpose": "Independent static/hash/receipt reconciliation and dependency import; no inference",
    "checks": checks,
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads((ROOT / path).read_text())


def require(name, condition):
    checks[name] = bool(condition)
    if not condition:
        raise AssertionError(name)


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


try:
    receipt["script_sha256"] = digest(Path(__file__))
    config_audit = read("docs/evidence/M0/mlx-lock-audit.json")
    require("configuration_hashes_match_tested_M0_receipt", all(
        digest(ROOT / path) == expected for path, expected in config_audit["files"].items()
    ))
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {norm(p["name"]): p for p in lock["package"]}
    old = tomllib.loads(subprocess.check_output(
        ["git", "show", "3761b3f:uv.lock"], text=True
    ))
    require("all_existing_core_dev_versions_unchanged", all(
        locked[norm(p["name"])]["version"] == p["version"] for p in old["package"]
    ))
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    require("core_python_constraint_unchanged", config["project"]["requires-python"] == ">=3.11")
    require("authoring_group_python313", config["tool"]["uv"]["dependency-groups"]["authoring-mlx"]["requires-python"] == ">=3.13,<3.14")
    entries = {}
    for line in (ROOT / "requirements-authoring-mlx.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([^= ]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})", line)
        if not match:
            raise AssertionError("invalid hash-locked wheel entry")
        name, version, sha = match.groups()
        if norm(name) in entries:
            raise AssertionError("duplicate dependency")
        entries[norm(name)] = (version, sha)
    deps = read("docs/evidence/M2/local-mlx-dependencies.json")
    require("exact34_wheel_entries", len(entries) == len(deps["wheels"]) == 34)
    wheel_bytes = 0
    for row in deps["wheels"]:
        path = ROOT / ".feature-rl/research/M2/downloads" / row["filename"]
        require("wheel:" + row["name"], (
            not path.is_symlink() and path.is_file()
            and path.stat().st_size == row["bytes"] and digest(path) == row["sha256"]
            and entries[norm(row["name"])] == (row["version"], row["sha256"])
            and locked[norm(row["name"])]["version"] == row["version"]
            and any(w["hash"] == "sha256:" + row["sha256"] for w in locked[norm(row["name"])].get("wheels", []))
        ))
        wheel_bytes += row["bytes"]
    receipt["verified_wheel_bytes"] = wheel_bytes
    require("wheel_download_budget", wheel_bytes == deps["total_bytes"] == 96889626 and wheel_bytes < deps["total_cap_bytes"])
    model = read("docs/evidence/M2/local-mlx-model-acquisition.json")
    model_dir = ROOT / ".feature-rl/research/M2/model/mlx-community--Qwen3-4B-Instruct-2507-4bit" / model["revision"]
    require("model_runtime_exact_positive_allowlist", set(p.name for p in model_dir.iterdir()) == set(model["runtime_positive_allowlist"]))
    model_bytes = 0
    for row in model["files"]:
        path = model_dir / row["path"]
        require("model_file:" + row["path"], not path.is_symlink() and path.is_file() and path.stat().st_size == row["bytes"] and digest(path) == row["sha256"])
        if row["published_sha256"] is not None:
            require("published_hash:" + row["path"], row["published_sha256"] == row["sha256"])
        model_bytes += row["bytes"]
    receipt["verified_model_bytes"] = model_bytes
    require("model_download_budget", model_bytes == model["actual_total_bytes"] == 2278969697 and model_bytes < model["total_cap_bytes"])
    smoke = read("docs/evidence/M2/local-mlx-smoke-attempt-1.json")
    events = [json.loads(line) for line in smoke["stdout"].splitlines()]
    tokens = [event for event in events if event["event"] == "token"]
    terminal = events[-1]
    receipt["smoke_terminal_event"] = terminal
    require("raw_token_sequence_exact16", len(tokens) == 16 and [e["position"] for e in tokens] == list(range(1, 17)))
    require("raw_token_ids_present", all(type(e["token_id"]) is int and e["token_id"] >= 0 for e in tokens))
    require("raw_smoke_truncated_length", terminal["finish_reason"] == "length" and terminal["truncated"] is True)
    require("smoke_cleanup_exit_output_bounds", smoke["execution"]["exit_status"] == 0 and smoke["execution"]["process_group_cleanup_verified"] is True and smoke["execution"]["combined_retained_bytes"] == len(smoke["stdout"].encode()) + len(smoke["stderr"].encode()) <= smoke["bounds"]["combined_output_cap_bytes"])
    require("smoke_time_and_observed_memory_below_limits", smoke["execution"]["wall_seconds"] < smoke["bounds"]["deadline_seconds"] and smoke["execution"]["max_usage"]["lifetime_max_physical_footprint_bytes"] < smoke["bounds"]["physical_footprint_cap_bytes"])
    receipt["smoke_sampled_physical_footprint_bytes"] = smoke["execution"]["max_usage"]["physical_footprint_bytes"]
    receipt["smoke_lifetime_physical_footprint_bytes"] = smoke["execution"]["max_usage"]["lifetime_max_physical_footprint_bytes"]
    reject = read("docs/evidence/M2/local-mlx-input-rejection.json")
    rejection = json.loads(reject["stdout"].splitlines()[-1])
    require("input4105_rejected_before_load_and_inference", rejection["actual_input_tokens"] == 4105 and rejection["max_input_tokens"] == 2048 and rejection["event"] == "input_rejected" and rejection["model_load_started"] is False and rejection["inference_started"] is False and reject["execution"]["process_group_cleanup_verified"])
    watchdog = read("docs/evidence/M2/local-mlx-memory-watchdog-proof.json")
    require("watchdog_memory_kill_cleanup", watchdog["execution"]["termination"] == "memory_cap" and watchdog["execution"]["exit_status"] == -9 and watchdog["execution"]["process_group_cleanup_verified"])
    receipt["memory_limit_qualification"] = "MLX allocator guideline; sampled watchdog termination with guard band, no zero-transient hard ceiling claim"
    for path in [ROOT / ".feature-rl/research/M2/local_mlx_probe.py", ROOT / ".feature-rl/research/M2/watchdog_canary.py"]:
        ast.parse(path.read_text())
    require("qualification_scripts_parse", True)
    supported = sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13) and platform.system() == "Darwin" and platform.machine() == "arm64" and int(platform.mac_ver()[0].split(".")[0]) >= 26
    require("qualified_platform_preflight", supported)
    receipt["platform"] = {"system": platform.system(), "macos": platform.mac_ver()[0], "machine": platform.machine(), "python": platform.python_version()}
    command = [sys.executable, "-c", "import importlib.metadata as m, json; import mlx.core as mx; import mlx_lm, transformers, numpy; print(json.dumps({'versions': {p:m.version(p) for p in ['mlx','mlx-metal','mlx-lm','transformers','numpy']}, 'metal_available':mx.metal.is_available()}))"]
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30, check=False)
    receipt["native_import"] = {"command": command, "environment_overrides": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}, "exit_status": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    require("native_dependency_import_no_inference", result.returncode == 0 and json.loads(result.stdout)["metal_available"] is True)
    receipt["status"] = "pass"
except Exception as exc:
    receipt["status"] = "fail"
    receipt["error"] = type(exc).__name__ + ": " + str(exc)
finally:
    receipt["wall_seconds"] = time.monotonic() - started
    OUT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "checks": len(checks), "receipt": str(OUT.relative_to(ROOT)), "wall_seconds": receipt["wall_seconds"]}))

if receipt["status"] != "pass":
    raise SystemExit(1)
