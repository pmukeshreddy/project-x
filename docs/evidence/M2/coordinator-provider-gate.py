#!/usr/bin/env python3
"""Capture the reviewed provider's fresh suite and one native integration check.

This coordinator driver does not retry either command. Native inference remains
guarded by coordinator-v3-native.py's permanent one-call claim. Run only after
the independent reviewer approves the named committed product.
"""

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


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/evidence/M2"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, check=True, timeout=15
    ).stdout.decode().strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-revision", required=True)
    parser.add_argument("--review-report", type=Path, required=True)
    args = parser.parse_args()
    review = args.review_report.resolve()
    if not review.is_relative_to(ROOT / "docs/reviews"):
        raise ValueError("review must be a retained project review report")
    product = git("rev-parse", args.product_revision)
    receipt_path = EVIDENCE / "coordinator-provider-gate.json"
    receipt = {
        "scope": "coordinator integration; unit diagnostics then synthetic nontraining native check",
        "product_revision": product,
        "coordinator_revision": git("rev-parse", "HEAD"),
        "review_report": review.relative_to(ROOT).as_posix(),
        "review_sha256": sha256(review.read_bytes()),
        "driver_sha256": sha256(Path(__file__).read_bytes()),
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): sha256(path.read_bytes())
            for path in sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
        "commands": [],
        "passed": False,
    }
    # Exclusive claim preserves a failed run instead of overwriting it on retry.
    with receipt_path.open("x") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    commands = (
        ("full-tests", [sys.executable, "-m", "pytest", "-q"]),
        ("native", [
            sys.executable, "docs/evidence/M2/coordinator-v3-native.py",
            "--product-revision", product,
            "--review-report", review.relative_to(ROOT).as_posix(),
        ]),
    )
    for name, command in commands:
        stdout_path = EVIDENCE / f"coordinator-provider-gate-{name}.stdout"
        stderr_path = EVIDENCE / f"coordinator-provider-gate-{name}.stderr"
        entry = {"name": name, "command": command,
                 "started_at": datetime.now(timezone.utc).isoformat()}
        started = time.monotonic()
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr)
        entry.update({
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.monotonic() - started,
            "exit_status": result.returncode,
            "stdout": stdout_path.relative_to(ROOT).as_posix(),
            "stdout_sha256": sha256(stdout_path.read_bytes()),
            "stderr": stderr_path.relative_to(ROOT).as_posix(),
            "stderr_sha256": sha256(stderr_path.read_bytes()),
        })
        receipt["commands"].append(entry)
        receipt["ended_at"] = datetime.now(timezone.utc).isoformat()
        receipt["passed"] = len(receipt["commands"]) == 2 and all(
            item["exit_status"] == 0 for item in receipt["commands"]
        )
        with receipt_path.open("w") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(entry), flush=True)
        if result.returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
