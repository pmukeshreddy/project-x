"""Inspect actual validation branch agreement; no provider or native execution."""
import hashlib
import json
from pathlib import Path
from typing import Literal

from feature_rl.contracts import StrictModel
from feature_rl.generation.provider import _build_literal_prevalidator


class Enabled(StrictModel):
    flag: Literal[True]


class Number(StrictModel):
    flag: int


class Mixed(StrictModel):
    mode: Enabled | Number


payload = b'{"mode":{"flag":1}}'
guard = _build_literal_prevalidator(Mixed)
checked = guard.validate_json(payload)
parsed = Mixed.model_validate_json(payload)
print(json.dumps({
    "scope": "read-only diagnostic of unfinished round3 source; no provider, runner or native call",
    "source_sha256": hashlib.sha256(Path("src/feature_rl/generation/provider.py").read_bytes()).hexdigest(),
    "input": json.loads(payload),
    "precheck_accepted": True,
    "actual_branch": type(parsed.mode).__name__,
    "actual_flag": parsed.mode.flag,
    "actual_flag_type": type(parsed.mode.flag).__name__,
    "numeric_to_boolean_coercion_observed": type(parsed.mode.flag) is bool,
}))
