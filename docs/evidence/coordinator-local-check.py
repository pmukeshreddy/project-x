"""Record a fresh non-GPU pytest run without overwriting earlier evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def bindings() -> dict[str, str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "src", "tests", "pyproject.toml", "requirements", "uv.lock"],
        cwd=ROOT,
    )
    return {name: sha(ROOT / name) for name in raw.decode().split("\0") if name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("pytest_arguments", nargs="*")
    args = parser.parse_args()
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = bindings()
    record = {
        "scope": "non_gpu_integration_check",
        "gpu_execution": "unverified",
        "experimental_results": "not_produced",
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "command": [sys.executable, "-m", "pytest", "-q", *args.pytest_arguments],
        "cwd": str(ROOT),
        "environment_overrides": {"PYTHONPATH": "src"},
        "started_at": utc(),
        "source_before": before,
    }
    started = time.monotonic()
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        result = subprocess.run(record["command"], cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"}, stdout=stdout, stderr=stderr)
    record.update(
        finished_at=utc(), wall_seconds=time.monotonic() - started, exit_status=result.returncode,
        source_after=bindings(), stdout_sha256=sha(output / "stdout.log"), stderr_sha256=sha(output / "stderr.log"),
    )
    record["sources_unchanged"] = record["source_before"] == record["source_after"]
    record["passed"] = result.returncode == 0 and record["sources_unchanged"]
    (output / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("revision", "started_at", "finished_at", "wall_seconds", "exit_status", "sources_unchanged", "passed")}))
    print((output / "stdout.log").read_text(errors="replace")[-2000:])
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
