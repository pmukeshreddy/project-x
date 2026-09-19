"""Narrow synthetic reproduction of Literal coercion in structured content."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from feature_rl.contracts import StrictModel
from feature_rl.generation.provider import _parse_envelope
from test_generation import request


class LiteralContent(StrictModel):
    confirmed: Literal[True]
    version: Literal[3]


req = request()
raw = {
    "response_id": req.response_id,
    "source_ids": [item.context_id for item in req.contexts],
    "requirement_ids": list(req.allowed_requirement_ids),
    "content": {"confirmed": 1, "version": 3.0},
}
root = Path(__file__).resolve().parents[3]
result = _parse_envelope(json.dumps(raw), req, LiteralContent)
receipt = {
    "producer": "coordinator /root",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "scope": "synthetic envelope parser diagnostic only; no runner/model/historical code",
    "source_revision": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=True).stdout.decode().strip(),
    "provider_sha256": hashlib.sha256((root / "src/feature_rl/generation/provider.py").read_bytes()).hexdigest(),
    "command": ["PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/coordinator-literal-envelope.py"],
    "input": raw,
    "accepted_content": result.model_dump(mode="json"),
    "input_types": {key: type(value).__name__ for key, value in raw["content"].items()},
    "output_types": {"confirmed": type(result.confirmed).__name__, "version": type(result.version).__name__},
    "defect_reproduced": result.confirmed is True and type(result.version) is int,
}
print(json.dumps(receipt, indent=2, sort_keys=True))
raise SystemExit(0 if receipt["defect_reproduced"] else 1)
