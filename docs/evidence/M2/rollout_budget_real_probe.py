#!/usr/bin/env python3
"""Run one bounded real rollout-budget probe and emit a JSON receipt to stdout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import selectors
import shutil
import signal
import subprocess
import tempfile
import time


ROOT = pathlib.Path(__file__).resolve().parent
CODEX = "/opt/homebrew/bin/codex"
OUTPUT_CAP = 1 << 20
DEADLINE_SECONDS = 20.0


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(staging: pathlib.Path, limit: int, reminder: int) -> list[str]:
    disabled = (
        "shell_tool",
        "unified_exec",
        "apps",
        "hooks",
        "multi_agent",
        "browser_use",
        "computer_use",
        "in_app_browser",
        "image_generation",
        "plugins",
        "remote_plugin",
        "skill_search",
        "skill_mcp_dependency_install",
        "workspace_dependencies",
        "tool_suggest",
        "goals",
        "memories",
        "sleep_tool",
        "view_image",
        "default_mode_request_user_input",
    )
    argv = [
        CODEX,
        "exec",
        "-C",
        str(staging),
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
    ]
    for feature in disabled:
        argv.extend(("--disable", feature))
    argv.extend(
        (
            "-c",
            "tools.experimental_request_user_input.enabled=false",
            "-c",
            'web_search="disabled"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "suppress_unstable_features_warning=true",
            "-c",
            "features.rollout_budget.enabled=true",
            "-c",
            f"features.rollout_budget.limit_tokens={limit}",
            "-c",
            f"features.rollout_budget.reminder_at_remaining_tokens=[{reminder}]",
            "-c",
            "features.rollout_budget.prefill_token_weight=0.0",
            "-c",
            "features.rollout_budget.sampling_token_weight=1.0",
            "-c",
            'model_reasoning_effort="low"',
            "--model",
            "gpt-5.6-luna",
            "--json",
            "--output-schema",
            str(staging / "rollout-budget-output.schema.json"),
            "--color",
            "never",
            "-",
        )
    )
    return argv


def run_bounded(argv: list[str], input_bytes: bytes) -> dict[str, object]:
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(input_bytes)
    process.stdin.close()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    total = 0
    termination = "process_exit"
    while selector.get_map():
        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            termination = "deadline"
            os.killpg(process.pid, signal.SIGKILL)
            break
        events = selector.select(timeout=min(remaining, 0.25))
        for key, _mask in events:
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                selector.unregister(key.fileobj)
                continue
            allowed = OUTPUT_CAP - total
            if len(data) > allowed:
                chunks[key.data].append(data[:allowed])
                total = OUTPUT_CAP
                termination = "output_cap"
                os.killpg(process.pid, signal.SIGKILL)
                break
            chunks[key.data].append(data)
            total += len(data)
        if termination == "output_cap":
            break
    selector.close()
    try:
        exit_status = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        exit_status = process.wait(timeout=2)
    elapsed = time.monotonic() - started
    stdout = b"".join(chunks["stdout"])
    stderr = b"".join(chunks["stderr"])
    return {
        "termination": termination,
        "exit_status": exit_status,
        "wall_seconds": elapsed,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "combined_output_bytes": len(stdout) + len(stderr),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    limit, reminder = (32, 16) if args.attempt == 1 else (1024, 512)
    prefix = f"feature-rl-m2-rollout-budget-{args.attempt}."
    staging = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir="/private/tmp"))
    prompt_path = staging / "rollout-budget-prompt.txt"
    schema_path = staging / "rollout-budget-output.schema.json"
    shutil.copyfile(ROOT / prompt_path.name, prompt_path)
    shutil.copyfile(ROOT / schema_path.name, schema_path)
    prompt = prompt_path.read_bytes()
    schema = schema_path.read_bytes()
    argv = command(staging, limit, reminder)
    execution = run_bounded(argv, prompt)
    events: list[object] = []
    malformed_lines: list[str] = []
    for line in str(execution["stdout"]).splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            malformed_lines.append(line)
    usage = None
    for event in events:
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            usage = event.get("usage")
    receipt = {
        "attempt": args.attempt,
        "staging_directory": str(staging),
        "staging_files": sorted(path.name for path in staging.iterdir()),
        "home_and_codex_home_inherited_unchanged": True,
        "deadline_seconds": DEADLINE_SECONDS,
        "combined_output_cap_bytes": OUTPUT_CAP,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "project_doc_max_bytes": 0,
        "rollout_budget": {
            "limit_tokens": limit,
            "prefill_token_weight": 0.0,
            "sampling_token_weight": 1.0,
            "reminder_at_remaining_tokens": [reminder],
        },
        "prompt": {"bytes": len(prompt), "sha256": digest(prompt)},
        "schema": {"bytes": len(schema), "sha256": digest(schema)},
        "argv": argv,
        "execution": execution,
        "event_types": [event.get("type") for event in events if isinstance(event, dict)],
        "malformed_stdout_lines": malformed_lines,
        "usage": usage,
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
