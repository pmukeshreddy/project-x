"""Bind the owner's fixed-source test evidence; no tests or inference rerun."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[3]
revision = "7239379ebf6921fd816b1bded9bb07a685b84c1a"
receipt_path = root / "docs/evidence/M2/provider-v3-fixround1-verification.json"
owner = json.loads(receipt_path.read_bytes())
checks = []
for relative, expected in owner["product_and_test_inventory"].items():
    actual = (root / relative).read_bytes()
    committed = subprocess.run(
        ["git", "show", f"{revision}:{relative}"], cwd=root,
        check=True, capture_output=True, timeout=15,
    ).stdout
    checks.append({
        "item": relative,
        "sha256": hashlib.sha256(actual).hexdigest(),
        "passed": actual == committed and len(actual) == expected["bytes"]
        and hashlib.sha256(actual).hexdigest() == expected["sha256"],
    })
for command in owner["commands"]:
    checks.append({"item": command["name"] + " exit status", "passed": command["exit_status"] == 0})
    for channel in ("stdout", "stderr"):
        data = (root / command[channel]).read_bytes()
        checks.append({
            "item": command[channel], "sha256": hashlib.sha256(data).hexdigest(),
            "passed": hashlib.sha256(data).hexdigest() == command[channel + "_sha256"],
        })
receipt = {
    "producer": "coordinator /root",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "source_revision": revision,
    "command": [".venv/bin/python", "docs/evidence/M2/coordinator-v3-binding.py"],
    "scope": "source and retained owner test-log binding only; no test or inference rerun",
    "owner_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    "checks": checks,
    "passed": bool(checks) and all(check["passed"] for check in checks),
}
print(json.dumps(receipt, indent=2, sort_keys=True))
raise SystemExit(0 if receipt["passed"] else 1)
