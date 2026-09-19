"""Bind M2's precommit checks to Git and reconcile preserved v1 archives.

Read-only product/evidence verification. No native inference or test rerun.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef

root = Path.cwd()
started = time.monotonic()
sha = lambda data: hashlib.sha256(data).hexdigest()
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
receipt = {
    "producer": "/root coordinator",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "revision": head,
    "review_subject": "8a4637552b87dbf5e7bfe57b0a843c3a59327dad",
    "command": ["env", "PYTHONPATH=src", ".venv/bin/python", "docs/evidence/M2/coordinator-v2-static.py"],
    "script_sha256": sha(Path(__file__).read_bytes()),
    "scope": "source/test-log binding and legacy artifact integrity only; no inference or test rerun",
    "checks": {},
}


def check(label, condition):
    receipt["checks"][label] = bool(condition)
    if not condition:
        raise AssertionError(label)


try:
    owner = json.loads((root / "docs/evidence/M2/provider-v2-verification.json").read_text())
    for name, expected in owner["product_hash_inventory"].items():
        data = (root / name).read_bytes()
        committed = subprocess.check_output(["git", "show", receipt["review_subject"] + ":" + name])
        check("source_binding:" + name, len(data) == expected["bytes"] and sha(data) == expected["sha256"] and data == committed)
    for run in owner["runs"]:
        for stream in ("stdout", "stderr"):
            check("test_log:" + run["name"] + ":" + stream, sha((root / run[stream]).read_bytes()) == run[stream + "_sha256"])
        check("recorded_test_exit:" + run["name"], run["exit_status"] == 0)
    store = ArtifactStore(root / ".feature-rl/research/M2/provider-production-store", ActorRole.REVIEWER)
    names = ["production-provider-input-rejection.json", "production-provider-smoke-attempt-1.json", "production-provider-smoke-attempt-2.json"]
    receipt["legacy_receipts"] = {}
    for name in names:
        path = root / "docs/evidence/M2" / name
        data = json.loads(path.read_text())
        record = data["record"]
        check("ten_archive_refs:" + name, len(record["archives"]) == 10)
        for archive_name, ref_data in record["archives"].items():
            ref = ArtifactRef.model_validate_json(json.dumps(ref_data))
            raw = store.get_bytes(ref)
            decoded = [json.loads(line) for line in raw.decode().splitlines()] if archive_name == "events" else json.loads(raw)
            check("archive:" + name + ":" + archive_name, decoded == data["archives"][archive_name])
        events = data["archives"]["events"]
        tokens = [event for event in events if event.get("event") == "token"]
        response = data["archives"]["response"]
        receipt["legacy_receipts"][name] = {
            "receipt_sha256": sha(path.read_bytes()),
            "token_events_including_eos": len(tokens),
            "last_token_id": tokens[-1]["token_id"] if tokens else None,
            "cpu_seconds_in_response": response.get("cpu_seconds"),
            "cpu_measurement_present_in_response": "cpu_seconds" in response,
            "cpu_seconds_in_cost": data["archives"]["cost"]["cpu_seconds"],
            "wall_seconds": response["wall_seconds"],
            "source_revision_and_original_UTC_binding": "unavailable; not reconstructed or inferred",
            "current_protocol_inference_evidence": False,
            "response_metadata_note": "Early protocol-v1 response archives omitted some measured fields; absence remains null, not inferred. Independent cost fields are identified separately.",
            "synthetic_context_reference_note": "Old driver used raw text SHA rather than publishing an M0 input object; these are engineering protocol receipts, not historical-source provenance evidence.",
        }
        if tokens:
            check("legacy_token_count:" + name, len(tokens) == data["usage"]["output_tokens"] == 83 and tokens[-1]["token_id"] == 151645)
    receipt["status"] = "pass"
except Exception as error:
    receipt["status"] = "fail"
    receipt["error"] = type(error).__name__ + ": " + str(error)
finally:
    receipt["wall_seconds"] = time.monotonic() - started
    out = root / "docs/evidence/M2/coordinator-v2-static.json"
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "checks": len(receipt["checks"]), "receipt": str(out.relative_to(root)), "wall_seconds": receipt["wall_seconds"]}))
if receipt["status"] != "pass":
    raise SystemExit(1)
