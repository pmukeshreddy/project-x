"""Read-only coordinator binding checks; no Docker or application execution."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef

ROOT = Path(__file__).resolve().parents[3]
TARGET = "a9fec98ce2fec8cd8bc30c245a1100156532e847"
INDEX = ROOT / "docs/evidence/M3/production/index.json"
OUT = ROOT / "docs/evidence/M3/coordinator-runtime-binding.json"
started = datetime.now(timezone.utc)
checks = []


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check(name, value):
    checks.append({"name": name, "passed": bool(value)})
    if not value:
        raise AssertionError(name)


index_bytes = INDEX.read_bytes()
index = json.loads(index_bytes)
check("index_matches_product_commit", index_bytes == subprocess.check_output(["git", "show", TARGET + ":docs/evidence/M3/production/index.json"], cwd=ROOT))
for section in ("source_hashes", "test_hashes"):
    for path, expected in index[section].items():
        data = (ROOT / path).read_bytes()
        check(section + ":" + path, sha(data) == expected and data == subprocess.check_output(["git", "show", TARGET + ":" + path], cwd=ROOT))
for item in index["evidence_files"]:
    data = (ROOT / item["path"]).read_bytes()
    check("evidence_file:" + item["path"], len(data) == item["bytes"] and sha(data) == item["sha256"])
suite = index["full_suite"]
log = (INDEX.parent / suite["log"]).read_text()
check("retained_full_suite_result", suite["exit_status"] == 0 and suite["passed"] == 337 and "337 passed in 69.16s" in log)
check("all_attempt_dispositions_retained", [item["status"] for item in index["records"]] == ["failed", "passed", "passed"])
receipts = 0
for record in index["records"]:
    store = ArtifactStore(ROOT / ".feature-rl/research/M3/production" / record["attempt"] / "artifacts", ActorRole.REVIEWER)
    for item in record["verified_receipts"]:
        ref = ArtifactRef.model_validate_json(json.dumps(item["ref"]))
        data = store.get_bytes(ref, max_envelope_bytes=8_388_608, max_payload_bytes=6_291_456)
        check("receipt_bytes:" + ref.sha256, len(data) == item["bytes"] and sha(data) == item["payload_sha256"])
        receipt = json.loads(data)
        check("receipt_bindings:" + ref.sha256, receipt["phase"] == item["phase"] and receipt["record"]["operation_id"] == item["operation_id"] and receipt["record"]["binding"] == item["binding"] and receipt["cleanup_verified"] is item["cleanup_verified"])
        receipts += 1
check("receipt_count", receipts == 23)
result = {
    "purpose": "Verify retained M3 source/test/log/CAS bindings without executing Docker or application code.",
    "command": ["env", "PYTHONPATH=src", ".venv/bin/python", "docs/evidence/M3/coordinator-runtime-binding.py"],
    "started_at_utc": started.isoformat(),
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    "target_revision": TARGET,
    "driver_sha256": sha(Path(__file__).read_bytes()),
    "index_sha256": sha(index_bytes),
    "checks_passed": len(checks),
    "checks": checks,
    "receipt_count": receipts,
    "exit_status": 0,
    "limitations": ["This binds retained observations; it does not rerun the earlier source revisions or Docker inventory.", "Independent product review remains authoritative for unresolved implementation defects; feature construction/qualification and training are still open."],
}
with OUT.open("x") as stream:
    json.dump(result, stream, indent=2, sort_keys=True)
    stream.write("\n")
print(json.dumps({"checks_passed": len(checks), "receipts": receipts, "receipt_sha256": sha(OUT.read_bytes())}))
