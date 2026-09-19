"""Trusted nested-model restoration diagnostic; no backend or worker call."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal


def main():
    source = Path("src/feature_rl/generation/provider.py")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    from pydantic import BaseModel, ConfigDict
    from feature_rl.contracts import StrictModel
    from feature_rl.generation.provider import _build_literal_prevalidator

    class Inner(BaseModel):
        model_config = ConfigDict(strict=True, extra="allow")
        x: int

    class Outer(StrictModel):
        flag: Literal[True]
        item: Inner

    raw = '{"flag":true,"item":{"x":1,"y":2}}'
    receipt = {
        "scope": "trusted working-source schema diagnostic; not full-provider verification",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": before,
        "raw": raw,
        "ordinary_json": Outer.model_validate_json(raw).model_dump(mode="json"),
        "backend_calls": 0,
        "runner_calls": 0,
        "native_calls": 0,
    }
    try:
        guard = _build_literal_prevalidator(Outer)
        receipt["preflight_admitted"] = True
        receipt["guarded_result"] = guard.validate_json(raw).model_dump(mode="json")
    except Exception as error:
        receipt["error_type"] = type(error).__name__
        receipt["error"] = str(error)
    receipt["source_unchanged_during_diagnostic"] = (
        hashlib.sha256(source.read_bytes()).hexdigest() == before
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
