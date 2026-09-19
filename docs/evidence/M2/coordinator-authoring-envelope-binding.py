"""Verify the completed sizing receipt without rerunning tokenization or inference."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef

root = Path(__file__).resolve().parents[3]
destination = root / "docs/evidence/M2/coordinator-authoring-envelope-binding.json"
started = datetime.now(timezone.utc)
checks = []


def check(name, condition):
    checks.append({"name": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)


def digest(data):
    return hashlib.sha256(data).hexdigest()


receipt_path = root / "docs/evidence/M2/authoring-envelope/receipt.json"
receipt_bytes = receipt_path.read_bytes()
check("owner_receipt_identity", digest(receipt_bytes) == "9f0fdbce03342b760fab77a217d568397ba9456eae8d32bafb7c879d7e5ea133")
receipt = json.loads(receipt_bytes)
for item in receipt["measurement_source_identities"] + receipt["raw_stream_identities"]:
    data = (root / item["path"]).read_bytes()
    check("file_binding:" + item["path"], len(data) == item["bytes"] and digest(data) == item["sha256"])
observed = json.loads((root / receipt["invocation"]["stdout"]).read_bytes())
check("raw_output_equals_receipt", observed == receipt["observed"])
check("recorded_exit_zero", receipt["invocation"]["exit_status"] == 0)
check("recorded_utc_order", receipt["invocation"]["started_at_utc"] <= observed["recorded_at_utc"] <= receipt["invocation"]["finished_at_utc"])
model_root = root / ".feature-rl/research/M2"
manifest = (model_root / "model-acquisition.json").read_bytes()
check("model_manifest_binding", digest(manifest) == observed["model_identity"]["manifest_sha256"])
model_directory = model_root / "model/mlx-community--Qwen3-4B-Instruct-2507-4bit" / observed["model_identity"]["revision"]
for name, item in observed["verified_tokenizer_assets"].items():
    data = (model_directory / name).read_bytes()
    check("tokenizer_asset_binding:" + name, len(data) == item["bytes"] and digest(data) == item["sha256"])
store = ArtifactStore(root / ".feature-rl/research/M1/production-store", ActorRole.AUTHOR)
payloads = {}
for name, item in observed["authoring_view"].items():
    ref = ArtifactRef.model_validate_json(json.dumps(item["reference"]))
    payloads[name] = store.get_bytes(ref, max_envelope_bytes=4_194_304, max_payload_bytes=2_097_152)
    check("authoring_input_binding:" + name, len(payloads[name]) == item["payload_bytes"] and digest(payloads[name]) == item["payload_sha256"])
with tarfile.open(fileobj=io.BytesIO(payloads["baseline"]), mode="r:") as archive:
    for template_name in ("sizing_template_with_license", "production_shaped_template_without_license"):
        template = observed[template_name]
        for context in template["contexts"]:
            if context["context_id"] == "REQUEST":
                data = payloads["request_evidence"]
            elif context["context_id"] == "LICENSE":
                data = payloads["license_text"]
            else:
                path, ranges = context["locator"].split(":")
                lines = archive.extractfile(path).read().decode().splitlines()
                spans = [tuple(map(int, pair.split("-"))) for pair in ranges.split(",")]
                data = "".join("\n".join(lines[start - 1:end]) + "\n" for start, end in spans).encode()
            check(template_name + ":context_binding:" + context["context_id"], len(data) == context["text_bytes"] and digest(data) == context["text_sha256"])
        check(template_name + ":chat_overhead", template["templated_input"]["tokens"] - template["provider_prompt"]["tokens_without_chat_template"] == 8)
check("measured_template_counts", observed["sizing_template_with_license"]["templated_input"]["tokens"] == 7669 and observed["production_shaped_template_without_license"]["templated_input"]["tokens"] == 7249)
check("production_template_excludes_license", all(context["context_id"] != "LICENSE" for context in observed["production_shaped_template_without_license"]["contexts"]))
result = {
    "purpose": "Coordinator verification of retained sizing evidence; no tokenization, model load, inference, application execution, Docker or network calls in this check.",
    "command": ["PYTHONPATH=src", ".venv/bin/python", "docs/evidence/M2/coordinator-authoring-envelope-binding.py"],
    "started_at_utc": started.isoformat(),
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    "workspace_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "driver_sha256": digest(Path(__file__).read_bytes()),
    "owner_receipt_sha256": digest(receipt_bytes),
    "checks": checks,
    "checks_passed": len(checks),
    "exit_status": 0,
    "limitations": ["Token counts are verified as the retained measurement's output, not independently recomputed.", "Neither template is the final semantic-proposal request; actual post-discovery sizing and native capacity remain open."],
}
with destination.open("x") as stream:
    json.dump(result, stream, indent=2, sort_keys=True)
    stream.write("\n")
print(json.dumps({"checks_passed": len(checks), "receipt": str(destination), "receipt_sha256": digest(destination.read_bytes())}))
