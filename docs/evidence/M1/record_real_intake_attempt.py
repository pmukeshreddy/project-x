"""Run one cached Click intake attempt and retain an exact timing receipt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--factory-revision", required=True)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    command = (
        sys.executable,
        "docs/evidence/M1/run_real_intake.py",
        "--workspace",
        str(args.workspace),
        "--factory-revision",
        args.factory_revision,
        "--recorded-at",
        args.recorded_at,
    )
    started_at = timestamp()
    started = time.monotonic()
    result = subprocess.run(command, cwd=args.workspace, capture_output=True)
    wall_seconds = time.monotonic() - started
    ended_at = timestamp()
    args.output.write_bytes(result.stdout)
    stderr_path = args.output.with_suffix(".stderr")
    stderr_path.write_bytes(result.stderr)
    receipt = {
        "command": list(command),
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_seconds": wall_seconds,
        "exit_status": result.returncode,
        "stdout_path": str(args.output),
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_path": str(stderr_path),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
