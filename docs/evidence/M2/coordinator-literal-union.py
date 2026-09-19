"""Synthetic check of the new Literal guard across model-union branches."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from feature_rl.contracts import StrictModel
from feature_rl.generation.provider import _parse_envelope
from test_generation import request


class Enabled(StrictModel):
    flag: Literal[True]


class Disabled(StrictModel):
    flag: Literal[False]


class UnionContent(StrictModel):
    mode: Enabled | Disabled


req = request()
raw = {
    "response_id": req.response_id,
    "source_ids": [item.context_id for item in req.contexts],
    "requirement_ids": list(req.allowed_requirement_ids),
    "content": {"mode": {"flag": 1}},
}
root = Path(__file__).resolve().parents[3]
result = _parse_envelope(json.dumps(raw), req, UnionContent)
receipt = {
    "producer": "coordinator /root",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "scope": "synthetic envelope parser diagnostic only; no runner/model/historical code",
    "source_revision": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=True).stdout.decode().strip(),
    "provider_sha256": hashlib.sha256((root / "src/feature_rl/generation/provider.py").read_bytes()).hexdigest(),
    "command": ["PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/coordinator-literal-union.py"],
    "input": raw,
    "accepted_content": result.model_dump(mode="json"),
    "selected_variant": type(result.mode).__name__,
    "input_type": type(raw["content"]["mode"]["flag"]).__name__,
    "output_type": type(result.mode.flag).__name__,
    "defect_reproduced": result.mode.flag is True,
}
print(json.dumps(receipt, indent=2, sort_keys=True))
raise SystemExit(0 if receipt["defect_reproduced"] else 1)
