"""Round-4 reviewer diagnostic; trusted schemas, test doubles, real M0 storage.

No worker/model/native inference is executed. A zero exit means the assertions
recorded below held, including the remaining allowed-chain boundary defect.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import Field, RootModel, ValidationInfo, model_validator
from pydantic_core import core_schema

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.generation import GenerationProviderError, LocalGenerationProvider
from feature_rl.generation.provider import _build_literal_prevalidator
from test_contracts_examples import examples
from test_generation import (
    BeforeFieldContent, CallbackUnionContent, EnumLiteralContent, FakeBackend,
    FakeRunner, JsonModeLiteralContent, MixedLiteralContent,
    NestedScalarUnionContent, PatternedLiteralContent, PostInitUnionContent,
    ReferencedBeforeContent, ScalarBooleanUnionContent, ScalarIntegerUnionContent,
    UnionLiteralContent, UnsupportedPlainLiteralContent, WrapFieldContent,
    request, worker_events,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REVISION = "44e9dcf1079a186a8a0432fa51333d1d07f6422f"
LIFECYCLE = []


class NormalizedInteger:
    @classmethod
    def __get_pydantic_core_schema__(cls, _source, _handler):
        return core_schema.chain_schema([
            core_schema.no_info_after_validator_function(int, core_schema.str_schema()),
            core_schema.int_schema(),
        ])


class AfterChainContent(c.StrictModel):
    flag: Literal[True]
    number: NormalizedInteger


class FlagRoot(RootModel[Literal[True]]):
    pass


class NestedRootContent(c.StrictModel):
    flag: FlagRoot


class LifecycleContent(c.StrictModel):
    flag: Literal[True]
    marker: str = Field(default_factory=lambda: LIFECYCLE.append("default") or "generated")

    def model_post_init(self, _context):
        LIFECYCLE.append("post_init")

    @model_validator(mode="after")
    def require_json(self, info: ValidationInfo):
        LIFECYCLE.append("after:" + info.mode)
        if info.mode != "json":
            raise ValueError("JSON validation mode required")
        return self


class CountingBackend(FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        return super().verify()


def typed_values(value):
    if isinstance(value, (c.StrictModel, RootModel)):
        return {"model": type(value).__name__, "fields": {
            name: typed_values(getattr(value, name)) for name in type(value).model_fields
        }}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "items": [typed_values(item) for item in value]}
    if isinstance(value, dict):
        return {"type": "dict", "items": {key: typed_values(item) for key, item in value.items()}}
    return {"type": type(value).__name__, "value": value.isoformat() if isinstance(value, datetime) else value}


def invoke(schema, raw_content, *, fail_schema_publication=False):
    events = [json.loads(line) for line in worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw_content
    events[-1]["output_text"] = json.dumps(envelope)
    backend = CountingBackend()
    runner = FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    with tempfile.TemporaryDirectory(prefix="case-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", c.ActorRole.AUTHOR)

        def archive(data, kind, visibility):
            if fail_schema_publication and kind == "generation-schema":
                raise OSError("reviewer injected unsupported-schema publication failure")
            return store.put_bytes(data, kind, visibility)

        try:
            response = LocalGenerationProvider(backend=backend, runner=runner, archive=archive).generate(request(), schema)
            record = response.record
            result = {"accepted": True, "content": response.content.model_dump(mode="json"),
                      "typed_content": typed_values(response.content),
                      "serialized_content": response.content.model_dump_json()}
        except GenerationProviderError as error:
            record = error.record
            result = {"accepted": False, "error": str(error), "error_code": record.error_code,
                      "known_cost": error.cost.model_dump(mode="json")}
            if fail_schema_publication:
                assert error.recovery is not None
                assert not record.publication_complete
                result["before_replay_archive_names"] = sorted(record.archives)
                record = error.replay_publication(store.put_bytes)
                result["recovered_error_code"] = record.error_code
                assert record.publication_complete and not record.generation_succeeded
        for ref in record.archives.values():
            store.get_bytes(ref)
        usage = json.loads(store.get_bytes(record.archives["usage"]))
        return result | {
            "schema_name": schema.__name__, "raw_content": raw_content,
            "record_success": record.success, "accepted_response_usage": usage["accepted_response_usage"],
            "backend_verification_calls": backend.verify_calls, "synthetic_runner_calls": len(runner.calls),
            "archive_names": sorted(record.archives), "all_archive_refs_resolved": True,
        }


def supported_cases():
    cases = {
        "scalar_boolean_as_bool": (ScalarBooleanUnionContent, {"flag": True}),
        "scalar_boolean_as_int": (ScalarBooleanUnionContent, {"flag": 1}),
        "scalar_integer_as_int": (ScalarIntegerUnionContent, {"value": 3}),
        "scalar_integer_as_float": (ScalarIntegerUnionContent, {"value": 3.0}),
        "nested_scalar_tuple": (NestedScalarUnionContent, {"values": [1, True, 2]}),
        "boolean_model_union_true": (UnionLiteralContent, {"mode": {"flag": True}}),
        "boolean_model_union_false": (UnionLiteralContent, {"mode": {"flag": False}}),
        "mixed_model_union_integer": (MixedLiteralContent, {"mode": {"flag": 1}}),
        "patterned_mapping": (PatternedLiteralContent, {"flags": {"k_flag": True}}),
        "nested_root_model": (NestedRootContent, {"flag": True}),
        "enum_tuple_UTC_transport": (EnumLiteralContent, {"visibility": "authoring", "values": ["alpha"], "recorded_at": "2026-09-19T10:00:00Z"}),
        "JSON_callback_mode": (JsonModeLiteralContent, {"flag": True}),
    }
    results = {}
    for name, (schema, raw) in cases.items():
        schema.model_validate_json(canonical_json(raw))
        result = invoke(schema, raw)
        assert result["accepted"] and result["accepted_response_usage"]
        results[name] = result
    assert results["scalar_boolean_as_int"]["typed_content"]["fields"]["flag"]["type"] == "int"
    assert results["scalar_boolean_as_bool"]["typed_content"]["fields"]["flag"]["type"] == "bool"
    assert results["scalar_integer_as_float"]["typed_content"]["fields"]["value"]["type"] == "float"
    assert results["scalar_integer_as_int"]["typed_content"]["fields"]["value"]["type"] == "int"
    assert results["scalar_integer_as_float"]["serialized_content"] == '{"value":3.0}'
    assert results["nested_scalar_tuple"]["serialized_content"] == '{"values":[1,true,2]}'
    assert results["mixed_model_union_integer"]["typed_content"]["fields"]["mode"]["model"] == "NumberAlternative"
    for schema, raw in (
        (UnionLiteralContent, {"mode": {"flag": 1}}),
        (PatternedLiteralContent, {"flags": {"k_flag": 1}}),
        (EnumLiteralContent, {"visibility": "authoring", "values": ["alpha"], "recorded_at": "2026-09-19T10:00:00+01:00"}),
    ):
        result = invoke(schema, raw)
        assert not result["accepted"] and not result["accepted_response_usage"]
        results["invalid_" + schema.__name__] = result
    LIFECYCLE.clear()
    result = invoke(LifecycleContent, {"flag": True})
    assert result["accepted"] and LIFECYCLE == ["default", "post_init", "after:json"]
    results["simple_lifecycle_runs_once_in_JSON_mode"] = result | {"lifecycle": list(LIFECYCLE)}
    for name in ("RequirementContract", "ScenarioPlan"):
        result = invoke(getattr(c, name), examples()[name])
        assert result["accepted"]
        results["synthetic_M0_" + name + "_transport"] = {
            key: result[key] for key in ("schema_name", "accepted", "record_success", "accepted_response_usage", "all_archive_refs_resolved")
        }
    return results


def unsupported_cases():
    cases = [
        (PostInitUnionContent, {"mode": {"flag": True}}),
        (BeforeFieldContent, {"flag": True, "number": "3"}),
        (WrapFieldContent, {"flag": True, "number": "3"}),
        (ReferencedBeforeContent, {"flag": True, "first": {"number": "3"}, "second": {"number": "4"}}),
        (CallbackUnionContent, {"mode": {"flag": True}}),
        (UnsupportedPlainLiteralContent, {"flag": True}),
    ]
    results = {}
    for schema, raw in cases:
        baseline = schema.model_validate_json(canonical_json(raw))
        result = invoke(schema, raw)
        assert result["error_code"] == "GenerationSchemaUnsupportedError"
        assert result["backend_verification_calls"] == result["synthetic_runner_calls"] == 0
        assert not result["record_success"] and not result["accepted_response_usage"]
        results[schema.__name__] = result | {"ordinary_JSON_baseline_accepted": True,
                                          "ordinary_JSON_result_type": type(baseline).__name__}
    replay = invoke(BeforeFieldContent, {"flag": True, "number": "3"}, fail_schema_publication=True)
    assert replay["recovered_error_code"] == "GenerationSchemaUnsupportedError"
    assert replay["backend_verification_calls"] == replay["synthetic_runner_calls"] == 0
    assert len(replay["archive_names"]) == 11
    results["unsupported_schema_publication_replay"] = replay
    return results


def remaining_after_chain():
    raw = {"flag": True, "number": "3"}
    baseline = AfterChainContent.model_validate_json(canonical_json(raw))
    assert baseline.number == 3 and type(baseline.number) is int
    guard = _build_literal_prevalidator(AfterChainContent)
    assert guard.requires_revalidation
    result = invoke(AfterChainContent, raw)
    assert not result["accepted"] and not result["accepted_response_usage"]
    assert result["error_code"] == "ValueError"
    assert result["backend_verification_calls"] == result["synthetic_runner_calls"] == 1
    return result | {
        "ordinary_JSON_baseline_accepted": True,
        "ordinary_JSON_typed_content": typed_values(baseline),
        "schema_preflight_accepted": True,
        "requires_revalidation": guard.requires_revalidation,
        "generated_JSON_schema": AfterChainContent.model_json_schema(),
        "chain_core_forms": ["function-after(str -> int)", "int"],
    }


def main():
    product = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    result = {
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "scope": "unit_diagnostic; backend/runner doubles, trusted schema fixtures and real ArtifactStore; no subprocess/model/inference",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "reviewed_revision": REVISION,
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round4/reproduce-round4.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in product},
        "supported_cases": supported_cases(),
        "unsupported_cases": unsupported_cases(),
        "remaining_R3_after_chain": remaining_after_chain(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
