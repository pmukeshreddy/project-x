"""Additional round-4 checks prompted by concrete coordinator reproductions.

Full-provider doubles and real temporary M0 storage only; no model or worker.
The earlier round4 diagnostic and raw output are preserved unchanged.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError
from pydantic_core import PydanticOmit

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, StrictModel
from feature_rl.generation import GenerationProviderError, LocalGenerationProvider
from feature_rl.generation.provider import _build_literal_prevalidator
from test_generation import FakeBackend, FakeRunner, request, worker_events


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REVISION = "44e9dcf1079a186a8a0432fa51333d1d07f6422f"
CALLBACKS = []


class Leaf(StrictModel):
    flag: Literal[True]
    label: str


class ModelSet(StrictModel):
    values: Annotated[set[Leaf], Field(min_length=2)]


class ModelFrozenSet(StrictModel):
    values: Annotated[frozenset[Leaf], Field(min_length=2)]


def omit_skip(value):
    CALLBACKS.append(value)
    if value == "skip":
        raise PydanticOmit
    return value


class OmitContent(StrictModel):
    flag: Literal[True]
    values: Annotated[list[Annotated[str, AfterValidator(omit_skip)]], Field(max_length=1)]


class CountingBackend(FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        return super().verify()


def invoke(schema, raw):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw
    events[-1]["output_text"] = json.dumps(envelope)
    runner = FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    backend = CountingBackend()
    with tempfile.TemporaryDirectory(prefix="restoration-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)
        try:
            response = LocalGenerationProvider(backend=backend, runner=runner, archive=store.put_bytes).generate(request(), schema)
            record = response.record
            result = {"accepted": True, "returned_container_type": type(response.content.values).__name__,
                      "returned_container_length": len(response.content.values),
                      "returned_content": response.content.model_dump(mode="json")}
            try:
                schema.model_validate(response.content)
            except ValidationError as error:
                result["ordinary_revalidation_accepts_returned_model"] = False
                result["ordinary_revalidation_error"] = str(error)
            else:
                result["ordinary_revalidation_accepts_returned_model"] = True
        except GenerationProviderError as error:
            record = error.record
            result = {"accepted": False, "error_code": record.error_code, "error": str(error)}
        for ref in record.archives.values():
            store.get_bytes(ref)
        usage = json.loads(store.get_bytes(record.archives["usage"]))
        return result | {"schema_name": schema.__name__, "raw_content": raw,
                         "record_success": record.success,
                         "accepted_response_usage": usage["accepted_response_usage"],
                         "backend_verification_calls": backend.verify_calls,
                         "synthetic_runner_calls": len(runner.calls),
                         "all_archive_refs_resolved": True}


def set_cases():
    results = {}
    valid = {"values": [{"flag": True, "label": "first"}, {"flag": True, "label": "second"}]}
    duplicates = {"values": [{"flag": True, "label": "same"}, {"flag": True, "label": "same"}]}
    for schema in (ModelSet, ModelFrozenSet):
        assert len(schema.model_validate_json(canonical_json(valid)).values) == 2
        positive = invoke(schema, valid)
        assert positive["accepted"] and positive["returned_container_length"] == 2
        assert positive["ordinary_revalidation_accepts_returned_model"]
        results[schema.__name__ + "_valid_control"] = positive
        try:
            schema.model_validate_json(canonical_json(duplicates))
        except ValidationError as error:
            baseline_error = str(error)
        else:
            raise AssertionError("ordinary validation must reject undersized deduplicated set")
        guard = _build_literal_prevalidator(schema)
        assert not guard.requires_revalidation
        inert = guard.validator.validate_json(canonical_json(duplicates))
        assert len(inert.values) == 2
        result = invoke(schema, duplicates)
        assert result["accepted"] and result["record_success"] and result["accepted_response_usage"]
        assert result["returned_container_length"] == 1
        assert not result["ordinary_revalidation_accepts_returned_model"]
        results[schema.__name__ + "_constraint_bypass"] = result | {
            "ordinary_JSON_baseline_accepted": False,
            "ordinary_JSON_baseline_error": baseline_error,
            "inert_container_length": len(inert.values),
            "minimum_required_items": 2,
            "generated_JSON_schema": schema.model_json_schema(),
        }
    return results


def omit_case():
    raw = {"flag": True, "values": ["skip", "keep"]}
    CALLBACKS.clear()
    ordinary = OmitContent.model_validate_json(canonical_json(raw))
    assert ordinary.values == ["keep"]
    baseline_callbacks = list(CALLBACKS)
    assert _build_literal_prevalidator(OmitContent).requires_revalidation
    CALLBACKS.clear()
    result = invoke(OmitContent, raw)
    assert not result["accepted"] and result["error_code"] == "ValueError"
    assert result["backend_verification_calls"] == result["synthetic_runner_calls"] == 1
    assert CALLBACKS == []
    return result | {"ordinary_JSON_baseline_accepted": True,
                     "ordinary_JSON_result": ordinary.model_dump(mode="json"),
                     "ordinary_JSON_callback_inputs": baseline_callbacks,
                     "provider_callback_inputs": list(CALLBACKS),
                     "schema_preflight_accepted": True}


def main():
    product = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    print(json.dumps({
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "scope": "unit_diagnostic; trusted schemas, backend/runner doubles and real ArtifactStore; no subprocess/model/inference",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_revision": REVISION,
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round4/reproduce-round4-restoration.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in product},
        "set_restoration": set_cases(),
        "remaining_R3_omit_callback": omit_case(),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
