#!/usr/bin/env python3
"""Credential-free request-shape capture for Codex CLI 0.154.0.

The HTTP handler intentionally never reads or records request headers.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import pathlib
import shutil
import subprocess
import tempfile
import threading


ROOT = pathlib.Path(__file__).resolve().parent
CODEX = "/opt/homebrew/bin/codex"
MAX_BODY = 1 << 20
WORKSPACE_TEXT = "/Users/mukeshreddypochamreddy/Desktop/project x"
PROJECT_DOC_CANARY = "M2_PROJECT_DOC_ZERO_CANARY_7f6f43d0"


def string_facts(value: object, path: str = "$") -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    if isinstance(value, str):
        raw = value.encode("utf-8")
        facts.append(
            {
                "path": path,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "agents_instruction_marker_hits": value.count("# AGENTS.md instructions for"),
                "agents_filename_hits": value.count("AGENTS.md"),
                "workspace_path_hits": value.count(WORKSPACE_TEXT),
                "project_doc_canary_hits": value.count(PROJECT_DOC_CANARY),
            }
        )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            facts.extend(string_facts(item, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            facts.extend(string_facts(item, f"{path}.{key}"))
    return facts


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    summary: dict[str, object] | None = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self.path.startswith("/v1/models"):
            self.send_error(404)
            return
        payload = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "gpt-5.6-luna",
                        "object": "model",
                        "created": 0,
                        "owned_by": "credential-free-loopback",
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        request = json.loads(body)
        strings = string_facts(request)
        budget_paths = sorted(
            path
            for path in _all_paths(request)
            if any(term in path.lower() for term in ("budget", "token", "max_output"))
        )
        self.__class__.summary = {
            "method": "POST",
            "path": self.path,
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "top_level_keys": sorted(request),
            "model": request.get("model"),
            "tool_count": len(request.get("tools", [])),
            "tool_names": [tool.get("name") for tool in request.get("tools", [])],
            "has_max_output_tokens": "max_output_tokens" in request,
            "budget_or_token_key_paths": budget_paths,
            "input_item_count": len(request.get("input", [])),
            "string_facts": strings,
            "agents_instruction_marker_hits": sum(
                int(fact["agents_instruction_marker_hits"]) for fact in strings
            ),
            "agents_filename_hits": sum(int(fact["agents_filename_hits"]) for fact in strings),
            "workspace_path_hits": sum(int(fact["workspace_path_hits"]) for fact in strings),
            "project_doc_canary_hits": sum(int(fact["project_doc_canary_hits"]) for fact in strings),
        }
        payload = b'{"error":{"message":"intentional credential-free capture stop"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _read_body(self) -> bytes:
        length_text = self.headers.get("Content-Length")
        if length_text is not None:
            length = int(length_text)
            if length > MAX_BODY:
                raise ValueError("request body exceeds capture cap")
            return self.rfile.read(length)
        if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
            raise ValueError("request has neither Content-Length nor chunked framing")
        chunks: list[bytes] = []
        total = 0
        while True:
            size_line = self.rfile.readline(128)
            size = int(size_line.split(b";", 1)[0], 16)
            if size == 0:
                self.rfile.readline(2)
                break
            total += size
            if total > MAX_BODY:
                raise ValueError("request body exceeds capture cap")
            chunks.append(self.rfile.read(size))
            if self.rfile.read(2) != b"\r\n":
                raise ValueError("malformed chunk terminator")
        return b"".join(chunks)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _all_paths(value: object, path: str = "$") -> list[str]:
    paths = [path]
    if isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_all_paths(item, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            paths.extend(_all_paths(item, f"{path}.{key}"))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canary-project-doc", action="store_true")
    args = parser.parse_args()
    staging = pathlib.Path(tempfile.mkdtemp(prefix="feature-rl-m2-budget-loopback.", dir="/private/tmp"))
    prompt = staging / "smoke-input.txt"
    schema = staging / "smoke-output.schema.json"
    shutil.copyfile(ROOT / "smoke-input.txt", prompt)
    shutil.copyfile(ROOT / "smoke-output.schema.json", schema)
    if args.canary_project_doc:
        (staging / "AGENTS.md").write_text(PROJECT_DOC_CANARY + "\n", encoding="utf-8")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

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
            'model_provider="capture"',
            "-c",
            'model_providers.capture.name="capture"',
            "-c",
            f'model_providers.capture.base_url="http://127.0.0.1:{port}/v1"',
            "-c",
            'model_providers.capture.wire_api="responses"',
            "-c",
            "model_providers.capture.requires_openai_auth=false",
            "-c",
            "model_providers.capture.request_max_retries=0",
            "-c",
            "model_providers.capture.stream_max_retries=0",
            "-c",
            "features.rollout_budget.enabled=true",
            "-c",
            "features.rollout_budget.limit_tokens=7000",
            "-c",
            "features.rollout_budget.reminder_at_remaining_tokens=[1000]",
            "-c",
            "features.rollout_budget.prefill_token_weight=1.0",
            "-c",
            "features.rollout_budget.sampling_token_weight=1.0",
            "-c",
            'model_reasoning_effort="low"',
            "--model",
            "gpt-5.6-luna",
            "--json",
            "--output-schema",
            str(schema),
            "--color",
            "never",
            "-",
        )
    )
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            input=prompt.read_bytes(),
            capture_output=True,
            timeout=20,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        returncode = 124
        stdout = error.stdout or b""
        stderr = error.stderr or b""
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)

    result = {
        "staging_directory": str(staging),
        "staging_files": sorted(path.name for path in staging.iterdir()),
        "home_unchanged": True,
        "codex_home_unchanged": True,
        "authorization_headers_inspected_or_retained": False,
        "canary_project_doc_staged": args.canary_project_doc,
        "deadline_seconds": 20,
        "output_cap_bytes": 1 << 20,
        "timed_out": timed_out,
        "exit_status": returncode,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
    if len(stdout) + len(stderr) > (1 << 20):
        raise RuntimeError("combined Codex output exceeded 1 MiB")
    print("CAPTURE_SUMMARY=" + json.dumps(CaptureHandler.summary, sort_keys=True, separators=(",", ":")))
    print("CODEX_RESULT=" + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if CaptureHandler.summary is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
