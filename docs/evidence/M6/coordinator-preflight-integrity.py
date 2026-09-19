"""Read-only coordinator integrity check; does not rerun crash probes."""
import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[3]
revision = "8b68d99bd7bf07b381f50e74ee84f7fe994b89fd"
path = root / "docs/evidence/M6/durability-preflight-receipts.json"
raw = path.read_bytes()
data = json.loads(raw)
captured_script = base64.b64decode(data["script"]["base64"], validate=True)
relative = "docs/evidence/M6/durability-preflight-probe.py"
committed = subprocess.run(
    ["git", "show", f"{revision}:{relative}"], cwd=root,
    capture_output=True, check=True, timeout=15,
).stdout
outer = json.loads((root / "docs/evidence/M6/durability-preflight-command.json").read_bytes())
checks = {
    "receipt_matches_report_hash": hashlib.sha256(raw).hexdigest() == "68871473cbf6010e4c080639860f9ae6d558b48021f0ea9350cce27f0b76f7b4",
    "receipt_length": len(raw) == 338213,
    "captured_script_hash": hashlib.sha256(captured_script).hexdigest() == data["script"]["sha256"],
    "captured_script_length": len(captured_script) == data["script"]["bytes"],
    "committed_and_live_script_match": captured_script == committed == (root / relative).read_bytes(),
    "all_reported_assertions_pass": len(data["checks"]) == data["check_count"] == 169 and all(item["passed"] is True for item in data["checks"]),
    "case_counts": len(data["cases"]) == 16 and len(data["negative_cases"]) == 3,
    "outer_command_zero": outer["exit_status"] == 0,
    "explicit_diagnostic_scope": data["scope"] == outer["scope"] == "TRUSTED_SYNTHETIC_DIAGNOSTIC_NO_REAL_TASK_APPROVAL_OR_REWARD",
}
print(json.dumps({
    "producer": "coordinator /root", "recorded_at": datetime.now(timezone.utc).isoformat(),
    "evidence_revision": revision,
    "command": [".venv/bin/python", "docs/evidence/M6/coordinator-preflight-integrity.py"],
    "scope": "retained receipt and script integrity only; no crash-probe or product-test rerun",
    "checks": checks, "passed": all(checks.values()),
}, indent=2, sort_keys=True))
raise SystemExit(0 if all(checks.values()) else 1)
