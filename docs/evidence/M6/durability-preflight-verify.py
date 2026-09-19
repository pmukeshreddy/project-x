#!/usr/bin/env python3
"""Read-only verification of M6 diagnostic evidence; no probe rerun or imports."""
import ast
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[3]
directory = root / "docs/evidence/M6"
receipt_path = directory / "durability-preflight-receipts.json"
receipt = json.loads(receipt_path.read_bytes())
command = json.loads((directory / "durability-preflight-command.json").read_bytes())
started = time.perf_counter()
checks = []


def check(name, truth):
    checks.append({"check": name, "passed": bool(truth)})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def walk(value, location):
    if isinstance(value, dict):
        if {"base64", "bytes", "sha256"} <= value.keys():
            data = base64.b64decode(value["base64"], validate=True)
            check(location, len(data) == value["bytes"] and digest(data) == value["sha256"])
        for key, item in value.items():
            walk(item, location + "/" + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk(item, location + "/" + str(index))


walk(receipt, "probe")
walk(command, "command")
check("driver_exit", command["exit_status"] == 0)
check("receipt_claim", receipt["passed"] and receipt["check_count"] == 169
      and len(receipt["checks"]) == 169 and all(c["passed"] for c in receipt["checks"]))
stdout = json.loads(base64.b64decode(command["stdout"]["base64"]))
check("command_binds_receipt", stdout["sha256"] == digest(receipt_path.read_bytes())
      and stdout["checks"] == 169 and stdout["passed"])
script = directory / "durability-preflight-probe.py"
check("executed_script_unchanged", script.read_bytes() == base64.b64decode(receipt["script"]["base64"]))
ast.parse(script.read_bytes())
live = []
for item in receipt["inputs"]:
    snapshot = (root / item["snapshot_path"]).read_bytes()
    committed = subprocess.check_output(["git", "show", item["head_before"] + ":" + item["path"]], cwd=root)
    check("input/" + item["path"], len(snapshot) == item["bytes"] and digest(snapshot) == item["sha256"]
          and snapshot == committed and item["matches_head_before"])
    current = (root / item["path"]).read_bytes()
    live.append({"path": item["path"], "sha256": digest(current), "matches_captured": current == snapshot})
check("matrix_size", len(receipt["cases"]) == 16 and len(receipt["negative_cases"]) == 3)
for case in receipt["cases"]:
    prefix = case["point"]
    before, after = case["before"], case["after"]
    check(prefix + "/exit", case["command"]["exit_status"] == (0 if prefix == "clean" else 91))
    journal = base64.b64decode(after["journal"]["base64"])
    expected = b"".join(json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                  allow_nan=False).encode() + b"\n" for e in after["events"])
    check(prefix + "/exact_projection", journal == expected and after["ack"] == len(after["events"]))
    check(prefix + "/preserved", all(before[key] == after[key] for key in ("events", "attempts", "jobs", "objects")))
    check(prefix + "/repeat", case["repeat"]["appended_bytes"] == 0
          and case["repeat"]["final"] == after["journal"])
    check(prefix + "/sqlite", after["integrity_check"] == [["ok"]] and after["foreign_key_check"] == []
          and after["pragmas"] == {"journal_mode": "delete", "synchronous": 3, "fullfsync": 1,
                                    "foreign_keys": 1, "busy_timeout": 1000})
for case in receipt["negative_cases"]:
    check(case["name"] + "/preserved_block", bool(case["error"]) and case["before"] == case["after"])
same = receipt["idempotence"]
check("same_attempt_no_new_cost_or_output", same["before"] == same["same_attempt_replay"])
check("three_attempts_single_selection", len(same["distinct_conflicting_output"]["attempts"]) == 3
      and same["before"]["jobs"] == same["distinct_conflicting_output"]["jobs"]
      and sum(e["kind"] == "attempt_observed" for e in same["distinct_conflicting_output"]["events"]) == 3)
check("ignored_state", subprocess.run(["git", "check-ignore", "--quiet", receipt["state"]], cwd=root).returncode == 0)
diff = subprocess.run(["git", "diff", "--check", "--", "docs/evidence/M6"], cwd=root, capture_output=True)
check("tracked_diff_whitespace", diff.returncode == 0)
for path in sorted(directory.iterdir()):
    if path.suffix in (".py", ".md"):
        check(path.name + "/whitespace", all(line == line.rstrip() for line in path.read_text().splitlines()))
result = {"scope": receipt["scope"], "mode": "STATIC_RECEIPT_INTEGRITY_ONLY_NO_EXPERIMENT_RERUN",
          "recorded_at": datetime.now(timezone.utc).isoformat(), "argv": [sys.executable, str(Path(__file__).resolve())],
          "head_observed": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
          "receipt_sha256": digest(receipt_path.read_bytes()), "live_inputs_observed": live,
          "owned_files": {p.name: {"bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
                          for p in sorted(directory.iterdir()) if p.is_file()},
          "checks": checks, "check_count": len(checks), "passed": all(c["passed"] for c in checks),
          "wall_seconds": time.perf_counter() - started}
output = directory / "durability-preflight-verification.json"
with output.open("x") as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write("\n")
print(json.dumps({"checks": len(checks), "passed": result["passed"],
                  "changed_live_inputs": [item for item in live if not item["matches_captured"]],
                  "sha256": digest(output.read_bytes())}))
raise SystemExit(0 if result["passed"] else 1)
