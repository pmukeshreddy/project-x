"""Independent round-3 diagnostics using test doubles and real artifact storage.

No worker, native library, model or historical/candidate program is executed.
Exit zero means the recorded assertions hold, including the remaining defects;
it is not provider approval or empirical authoring/qualification evidence.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.generation import GenerationProviderError, GenerationRequest, LocalGenerationProvider
from feature_rl.generation.protocol import EVENT_MODELS
from feature_rl.generation.provider import _build_literal_prevalidator, _decode_event
from test_contracts_examples import examples
from test_generation import (
    CallbackUnionContent, EnumLiteralContent, FakeBackend, FakeRunner,
    MixedLiteralContent, NestedLiteralContent, PatternedLiteralContent,
    SmokeContent, UnionLiteralContent, UnsupportedPlainLiteralContent,
    limits, request, worker_events,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REVISION = "20af743778815635bb4af521164199bd8f09dcb8"


class ScalarBooleanUnion(c.StrictModel):
    flag: Literal[True] | int


class ScalarIntegerUnion(c.StrictModel):
    value: Literal[3] | float


LIFECYCLE = []


class DecliningPostInit(c.StrictModel):
    flag: Literal[True]

    def model_post_init(self, _context):
        LIFECYCLE.append("declining_post_init")
        raise ValueError("declined in post init")


class AcceptingAlternative(c.StrictModel):
    flag: bool


class PostInitUnion(c.StrictModel):
    branch: DecliningPostInit | AcceptingAlternative


class BeforeFieldContent(c.StrictModel):
    flag: Literal[True]
    number: int

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, value):
        LIFECYCLE.append("before_number:" + type(value).__name__)
        return int(value) if isinstance(value, str) else value


class LifecycleContent(c.StrictModel):
    flag: Literal[True]
    marker: str = Field(default_factory=lambda: LIFECYCLE.append("default") or "generated")

    def model_post_init(self, _context):
        LIFECYCLE.append("post_init")

    @model_validator(mode="after")
    def record_after(self):
        LIFECYCLE.append("after")
        return self


class CountingBackend(FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        return super().verify()


def fixture():
    return [json.loads(line) for line in worker_events().splitlines()]


def typed_values(value):
    if isinstance(value, c.StrictModel):
        return {"model": type(value).__name__, "fields": {
            name: typed_values(getattr(value, name)) for name in type(value).model_fields
        }}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "items": [typed_values(item) for item in value]}
    if isinstance(value, dict):
        return {"type": "dict", "items": {name: typed_values(item) for name, item in value.items()}}
    return {"type": type(value).__name__, "value": value.isoformat() if isinstance(value, datetime) else value}


def invoke(schema, content):
    events = fixture()
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = content
    events[-1]["output_text"] = json.dumps(envelope)
    backend = CountingBackend()
    runner = FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    with tempfile.TemporaryDirectory(prefix="provider-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", c.ActorRole.AUTHOR)
        try:
            response = LocalGenerationProvider(backend=backend, runner=runner, archive=store.put_bytes).generate(request(), schema)
            record = response.record
            result = {"accepted": True, "parsed_content": response.content.model_dump(mode="json"),
                      "typed_content": typed_values(response.content)}
        except GenerationProviderError as error:
            record = error.record
            result = {"accepted": False, "error": str(error), "error_code": record.error_code}
        for ref in record.archives.values():
            store.get_bytes(ref)
        usage = json.loads(store.get_bytes(record.archives["usage"]))
        return result | {
            "raw_content": content, "schema_name": schema.__name__,
            "record_success": record.success, "accepted_response_usage": usage["accepted_response_usage"],
            "backend_verification_calls": backend.verify_calls, "synthetic_runner_calls": len(runner.calls),
            "archive_names": sorted(record.archives), "all_archive_refs_resolved": True,
        }


def repaired_content_cases():
    cases = {
        "boolean_union_true": (UnionLiteralContent, {"mode": {"flag": True}}, True),
        "boolean_union_false": (UnionLiteralContent, {"mode": {"flag": False}}, True),
        "boolean_union_numeric_rejected": (UnionLiteralContent, {"mode": {"flag": 1}}, False),
        "patterned_mapping_correct": (PatternedLiteralContent, {"flags": {"k_flag": True}}, True),
        "patterned_mapping_numeric_rejected": (PatternedLiteralContent, {"flags": {"k_flag": 1}}, False),
        "mixed_model_union_returns_integer_branch": (MixedLiteralContent, {"mode": {"flag": 1}}, True),
        "flat_referenced_literal_wrong_float": (NestedLiteralContent, {"leaf": {"confirmed": True, "version": 3.0}}, False),
        "M0_enum_tuple_UTC_compatible": (EnumLiteralContent, {"visibility": "authoring", "values": ["alpha", "beta"], "recorded_at": "2026-09-19T10:00:00Z"}, True),
        "M0_nonUTC_rejected": (EnumLiteralContent, {"visibility": "authoring", "values": ["alpha"], "recorded_at": "2026-09-19T10:00:00+01:00"}, False),
    }
    results = {}
    for name, (schema, content, expected) in cases.items():
        result = invoke(schema, content)
        assert result["accepted"] is expected
        assert result["record_success"] is expected
        assert result["accepted_response_usage"] is expected
        results[name] = result
    branch = results["mixed_model_union_returns_integer_branch"]["typed_content"]["fields"]["mode"]
    assert branch["model"] == "NumberAlternative" and branch["fields"]["flag"]["type"] == "int"
    # Existing M0 fixtures are complete synthetic schema diagnostics, not claims
    # that these example tasks were generated, sourced, executed or qualified.
    for name in ("RequirementContract", "ScenarioPlan"):
        result = invoke(getattr(c, name), examples()[name])
        assert result["accepted"]
        results["synthetic_M0_" + name + "_transport"] = {
            key: result[key] for key in ("accepted", "record_success", "accepted_response_usage", "schema_name", "all_archive_refs_resolved")
        }
    return results


def remaining_scalar_union_cases():
    results = {}
    for name, schema, raw, field, expected_before, actual_after in (
        ("boolean_scalar_union", ScalarBooleanUnion, {"flag": 1}, "flag", "int", "bool"),
        ("integer_scalar_union", ScalarIntegerUnion, {"value": 3.0}, "value", "float", "int"),
    ):
        guard = _build_literal_prevalidator(schema)
        inert = guard.validator.validate_json(canonical_json(raw))
        assert type(getattr(inert, field)).__name__ == expected_before
        result = invoke(schema, raw)
        assert result["accepted"] and result["accepted_response_usage"]
        assert result["typed_content"]["fields"][field]["type"] == actual_after
        results[name] = result | {"inert_prevalidation_selected_scalar_type": expected_before,
                                  "final_scalar_type": actual_after,
                                  "generated_schema": schema.model_json_schema()}
    return results


def callback_cases():
    results = {}
    for schema, raw in (
        (PostInitUnion, {"branch": {"flag": True}}),
        (BeforeFieldContent, {"flag": True, "number": "3"}),
    ):
        LIFECYCLE.clear()
        baseline = schema.model_validate_json(canonical_json(raw))
        baseline_calls = list(LIFECYCLE)
        assert _build_literal_prevalidator(schema) is not None
        LIFECYCLE.clear()
        result = invoke(schema, raw)
        assert not result["accepted"]
        assert result["synthetic_runner_calls"] == 1 and result["backend_verification_calls"] == 1
        results[schema.__name__] = result | {
            "ordinary_JSON_validation_accepted": True,
            "ordinary_JSON_typed_content": typed_values(baseline),
            "ordinary_JSON_lifecycle": baseline_calls,
            "provider_lifecycle": list(LIFECYCLE),
            "schema_preflight_accepted": True,
        }
    LIFECYCLE.clear()
    result = invoke(LifecycleContent, {"flag": True})
    assert result["accepted"] and LIFECYCLE == ["default", "post_init", "after"]
    results["simple_lifecycle_single_execution"] = result | {"provider_lifecycle": list(LIFECYCLE)}
    for schema in (CallbackUnionContent, UnsupportedPlainLiteralContent):
        result = invoke(schema, {"flag": True})
        assert result["error_code"] == "GenerationSchemaUnsupportedError"
        assert result["backend_verification_calls"] == result["synthetic_runner_calls"] == 0
        results["explicit_refusal_" + schema.__name__] = result
    return results


def identity_and_recovery_cases():
    results = {}
    original = request()
    for field in ("request_id", "response_id", "prompt_id"):
        for length in (129, 1_048_577):
            try:
                GenerationRequest.model_validate(original.model_dump() | {field: "R" * length})
            except ValidationError:
                results[f"{field}_length_{length}_rejected"] = True
            else:
                raise AssertionError("oversized ID passed request validation")
        for mechanism in ("model_copy", "model_construct"):
            forged = (original.model_copy(update={field: "R" * 129}) if mechanism == "model_copy"
                      else GenerationRequest.model_construct(**(original.__dict__ | {field: "R" * 129})))
            backend = CountingBackend()
            runner = FakeRunner()
            writes = []
            try:
                LocalGenerationProvider(backend=backend, runner=runner, archive=lambda *args: writes.append(args)).generate(forged, SmokeContent)
            except ValidationError:
                assert not writes and not runner.calls and backend.verify_calls == 0
                results[f"{field}_{mechanism}_rejected_before_work"] = True
            else:
                raise AssertionError("forged oversized ID passed public provider boundary")
    bounded_request = GenerationRequest.model_validate(
        request(generation_limits=limits(stdin_bytes=4096)).model_dump()
        | {"request_id": "R" * 128, "response_id": "S" * 128, "prompt_id": "P" * 128,
           "instruction": "Attributable diagnostic instruction. " * 200}
    )
    for fail_kind in (None, "generation-attempt", "generation-preflight", "generation-status"):
        backend = CountingBackend()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory(prefix="bounded-replay-", dir=HERE) as directory:
            store = ArtifactStore(Path(directory) / "objects", c.ActorRole.AUTHOR)

            def archive(data, kind, visibility):
                if kind == fail_kind:
                    raise OSError("reviewer injected publication failure")
                return store.put_bytes(data, kind, visibility)

            try:
                LocalGenerationProvider(backend=backend, runner=runner, archive=archive).generate(bounded_request, SmokeContent)
            except GenerationProviderError as error:
                retained_sizes = ({payload.name: len(payload.data) for payload in error.recovery.payloads}
                                  if error.recovery is not None else {})
                if fail_kind is None:
                    assert error.recovery is None
                    final_record = error.record
                else:
                    assert error.recovery is not None
                    assert max(retained_sizes.values()) < 4096
                    final_record = error.replay_publication(store.put_bytes)
                assert not final_record.success and not final_record.generation_succeeded
                assert final_record.publication_complete and final_record.error_code == "GenerationInputLimitError"
                assert set(final_record.archives) == {"attempt", "preflight", "cost", "status"}
                resolved = {name: store.get_bytes(ref) for name, ref in final_record.archives.items()}
                sizes = {name: len(data) for name, data in resolved.items()}
                assert max(sizes.values()) < 4096
                preflight = json.loads(resolved["preflight"])
                assert preflight["request_sha256"] == hashlib.sha256(canonical_json(bounded_request.model_dump(mode="json"))).hexdigest()
                assert preflight["request_id"] == bounded_request.request_id
                assert preflight["response_id"] == bounded_request.response_id
                assert preflight["prompt_id"] == bounded_request.prompt_id
                assert not runner.calls and backend.verify_calls == 0
                results["maximum_ID_preflight_" + (fail_kind or "no_failure")] = {
                    "final_archive_bytes": sizes, "retained_recovery_payload_bytes": retained_sizes,
                    "all_refs_resolved": True, "request_hash_matches": True,
                    "all_three_ID_lengths": 128, "backend_verification_calls": 0, "runner_calls": 0,
                    "publication_complete": final_record.publication_complete,
                    "generation_succeeded": final_record.generation_succeeded,
                    "error_code": final_record.error_code,
                }
            else:
                raise AssertionError("expected input limit failure")
    return results


def valid_event_baselines_and_bounds():
    events = {item["event"]: item for item in fixture()}
    events["identity_validated"]["prompt_sha256"] = "a" * 64
    events["identity_validated"]["worker_source_sha256"] = "b" * 64
    events["input_rejected"] = {
        "protocol_version": 3, "request_id": "REQ_CALL_1", "response_id": "RESP_1",
        "prompt_id": "PROMPT_1", "event": "input_rejected", "actual_input_tokens": 381,
        "max_input_tokens": 16, "model_load_started": False, "inference_started": False,
    }
    checked = []
    for name, model in EVENT_MODELS.items():
        assert _decode_event(json.dumps(events[name])).event == name
        for field in ("request_id", "response_id", "prompt_id"):
            maximum = events[name] | {field: "R" * 128}
            assert getattr(_decode_event(json.dumps(maximum)), field) == "R" * 128
            try:
                _decode_event(json.dumps(events[name] | {field: "R" * 129}))
            except ValueError:
                checked.append(name + "." + field)
            else:
                raise AssertionError("oversized event ID accepted")
    assert len(checked) == 21
    return {"valid_baselines": sorted(events), "max_length_IDs_accepted": True, "oversized_ID_mutations_rejected": checked}


def main():
    product = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    result = {
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "scope": "unit_diagnostic; backend/runner doubles and real ArtifactStore; no subprocess/model/inference",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "reviewed_revision": REVISION,
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round3/reproduce-round3.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in product},
        "repaired_content": repaired_content_cases(),
        "remaining_scalar_union_coercions": remaining_scalar_union_cases(),
        "callback_semantics": callback_cases(),
        "identity_and_recovery": identity_and_recovery_cases(),
        "event_baselines_and_bounds": valid_event_baselines_and_bounds(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
