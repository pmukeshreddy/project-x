"""Trusted schema admission diagnostic; no provider/runner/native call."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Literal

from feature_rl.contracts import StrictModel
from feature_rl.generation.provider import _build_literal_prevalidator


class DecimalLiteral(StrictModel):
    value: Literal[Decimal("1")]


def main():
    source = Path("src/feature_rl/generation/provider.py")
    receipt = {
        "scope": "narrow trusted schema diagnostic, working source; not a full-provider or native result",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "json_schema": DecimalLiteral.model_json_schema(),
        "ordinary_json_results": [],
        "backend_calls": 0,
        "runner_calls": 0,
        "native_calls": 0,
    }
    try:
        guard = _build_literal_prevalidator(DecimalLiteral)
        receipt["preflight"] = {"admitted": True, "guard_present": guard is not None}
    except Exception as error:
        receipt["preflight"] = {
            "admitted": False, "error_type": type(error).__name__, "message": str(error)
        }
    for raw in ['{"value":1}', '{"value":1.0}', '{"value":"1"}', '{"value":true}']:
        item = {"raw": raw}
        try:
            value = DecimalLiteral.model_validate_json(raw).value
            item.update(accepted=True, value_repr=repr(value), value_type=type(value).__name__)
        except Exception as error:
            item.update(accepted=False, error_type=type(error).__name__)
        receipt["ordinary_json_results"].append(item)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
