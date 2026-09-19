"""Independent round-2 boundary diagnostics; no worker, model, or inference.

Provider calls use the declared test backend/runner doubles. Artifact references
are published and resolved using the real M0 ArtifactStore in temporary folders.
Exit zero means the assertions below reproduced the recorded outcomes, including
the two remaining defects; it does not mean provider approval.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from pydantic import Field

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, StrictModel
from feature_rl.generation import GenerationProviderError, GenerationRequest, LocalGenerationProvider
from feature_rl.generation.protocol import EVENT_MODELS
from feature_rl.generation.provider import _decode_event
from test_generation import FakeBackend, FakeRunner, SmokeContent, limits, request, worker_events


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REVISION = "3f859ca991c8965fda48e9d95588e9be12b0fdab"


class Enabled(StrictModel):
    flag: Literal[True]


class Disabled(StrictModel):
    flag: Literal[False]


class NestedUnion(StrictModel):
    mode: Enabled | Disabled


class PatternedMapping(StrictModel):
    flags: dict[Annotated[str, Field(pattern="^k_")], Literal[True]]


class FlatLiterals(StrictModel):
    flag: Literal[True]
    version: Literal[3]


def fixture():
    return [json.loads(line) for line in worker_events().splitlines()]


def event_literal_checks():
    events = {event["event"]: event for event in fixture()}
    # These are schema-decoding checks; fix the fixture placeholders so each
    # unchanged baseline really decodes before testing its one-field mutation.
    events["identity_validated"]["prompt_sha256"] = "a" * 64
    events["identity_validated"]["worker_source_sha256"] = "b" * 64
    events["input_rejected"] = {
        "protocol_version": 3, "request_id": "REQ_CALL_1", "response_id": "RESP_1",
        "prompt_id": "PROMPT_1", "event": "input_rejected",
        "actual_input_tokens": 381, "max_input_tokens": 16,
        "model_load_started": False, "inference_started": False,
    }
    results = []
    exact_fields = []
    for name, model in EVENT_MODELS.items():
        baseline = events[name]
        assert _decode_event(json.dumps(baseline)).event == name
        for field, schema in model.model_json_schema()["properties"].items():
            expected = schema.get("const")
            if type(expected) not in {bool, int}:
                continue
            exact_fields.append(name + "." + field)
            substitutes = [int(expected), float(expected)] if type(expected) is bool else [float(expected)]
            for wrong in substitutes:
                try:
                    _decode_event(json.dumps(baseline | {field: wrong}))
                except ValueError as error:
                    results.append({"event": name, "field": field, "raw": wrong,
                                    "raw_type": type(wrong).__name__, "rejected": True,
                                    "error": str(error)})
                else:
                    raise AssertionError(f"wrong raw event literal accepted: {name}.{field}")
    assert len(exact_fields) == 15
    assert len(results) == 23
    return {"valid_baselines": sorted(events), "exact_fields": exact_fields, "mutations": results}


def envelope_case(schema, content):
    events = fixture()
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = content
    events[-1]["output_text"] = json.dumps(envelope)
    runner = FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    with tempfile.TemporaryDirectory(prefix="envelope-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)
        provider = LocalGenerationProvider(backend=FakeBackend(), runner=runner, archive=store.put_bytes)
        try:
            response = provider.generate(request(), schema)
            record = response.record
            outcome = {"accepted": True, "parsed_content": response.content.model_dump(mode="json")}
        except GenerationProviderError as error:
            record = error.record
            outcome = {"accepted": False, "error": str(error)}
        for ref in record.archives.values():
            store.get_bytes(ref)
        usage = json.loads(store.get_bytes(record.archives["usage"]))
        return outcome | {
            "schema": schema.model_json_schema(), "raw_content": content,
            "record_success": record.success,
            "accepted_response_usage": usage["accepted_response_usage"],
            "synthetic_runner_calls": len(runner.calls),
            "all_archive_refs_resolved": True,
        }


def structured_content_checks():
    cases = {
        "flat_correct": (FlatLiterals, {"flag": True, "version": 3}, True),
        "flat_numeric_boolean_rejected": (FlatLiterals, {"flag": 1, "version": 3}, False),
        "flat_float_integer_rejected": (FlatLiterals, {"flag": True, "version": 3.0}, False),
        "union_enabled_correct": (NestedUnion, {"mode": {"flag": True}}, True),
        "union_disabled_correct": (NestedUnion, {"mode": {"flag": False}}, True),
        "union_numeric_boolean_STILL_ACCEPTED": (NestedUnion, {"mode": {"flag": 1}}, True),
        "patterned_mapping_correct": (PatternedMapping, {"flags": {"k_flag": True}}, True),
        "patterned_mapping_numeric_boolean_STILL_ACCEPTED": (PatternedMapping, {"flags": {"k_flag": 1}}, True),
    }
    results = {}
    for name, (schema, content, expected_acceptance) in cases.items():
        result = envelope_case(schema, content)
        assert result["accepted"] is expected_acceptance
        assert result["record_success"] is expected_acceptance
        assert result["accepted_response_usage"] is expected_acceptance
        results[name] = result
    assert results["union_numeric_boolean_STILL_ACCEPTED"]["parsed_content"] == {"mode": {"flag": True}}
    assert results["patterned_mapping_numeric_boolean_STILL_ACCEPTED"]["parsed_content"] == {"flags": {"k_flag": True}}
    return results


class NeverVerifiedBackend(FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        raise AssertionError("preflight must not verify the backend")


def preflight_case(generation_request, fail_kind=None):
    runner = FakeRunner()
    backend = NeverVerifiedBackend()
    attempts = []
    with tempfile.TemporaryDirectory(prefix="preflight-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)

        def archive(data, kind, visibility):
            attempts.append({"kind": kind, "bytes": len(data)})
            if kind == fail_kind:
                raise OSError("reviewer injected preflight publication failure")
            return store.put_bytes(data, kind, visibility)

        provider = LocalGenerationProvider(backend=backend, runner=runner, archive=archive)
        try:
            provider.generate(generation_request, SmokeContent)
        except GenerationProviderError as error:
            assert not runner.calls and backend.verify_calls == 0
            before = error.record
            assert not before.success and not before.generation_succeeded
            assert error.cost.measurement == "unknown"
            assert error.cost.input_tokens is None and error.cost.output_tokens is None
            retained_sizes = ({payload.name: len(payload.data) for payload in error.recovery.payloads}
                              if error.recovery is not None else {})
            if fail_kind is None:
                assert before.publication_complete and error.recovery is None
                final_record = before
            else:
                assert not before.publication_complete and error.recovery is not None
                final_record = error.replay_publication(store.put_bytes)
                assert not final_record.success and not final_record.generation_succeeded
                assert final_record.publication_complete
            assert final_record.error_code == "GenerationInputLimitError"
            assert set(final_record.archives) == {"attempt", "preflight", "cost", "status"}
            resolved = {name: store.get_bytes(ref) for name, ref in final_record.archives.items()}
            preflight = json.loads(resolved["preflight"])
            attempt = json.loads(resolved["attempt"])
            status = json.loads(resolved["status"])
            assert preflight["execution_started"] is False
            assert preflight["request_sha256"] == hashlib.sha256(canonical_json(generation_request.model_dump(mode="json"))).hexdigest()
            assert preflight["request_sha256"] == attempt["request_sha256"]
            assert preflight["output_schema_sha256"] == attempt["output_schema_sha256"]
            assert preflight["request_id"] == generation_request.request_id
            assert status["error_type"] == "GenerationInputLimitError"
            return {
                "validated_request": True, "stdin_cap_bytes": generation_request.limits.stdin_bytes,
                "request_id_bytes": len(generation_request.request_id.encode()),
                "observed_input_bytes": preflight["observed_bytes"],
                "oversized_components": preflight["oversized_components"],
                "injected_failure_kind": fail_kind, "archive_attempts": attempts,
                "before_publication_complete": before.publication_complete,
                "before_archive_names": sorted(before.archives),
                "retained_recovery_payload_bytes": retained_sizes,
                "final_archive_bytes": {name: len(data) for name, data in resolved.items()},
                "record_request_id_bytes": len(final_record.request_id.encode()),
                "request_hash_matches_actual_request": True,
                "final_generation_succeeded": final_record.generation_succeeded,
                "final_publication_complete": final_record.publication_complete,
                "final_error_code": final_record.error_code,
                "cost": error.cost.model_dump(mode="json"),
                "all_archive_refs_resolved": True,
                "backend_verification_calls": backend.verify_calls,
                "synthetic_runner_calls": len(runner.calls),
            }
        raise AssertionError("expected typed oversized-input failure")


def preflight_checks():
    instruction_request = GenerationRequest.model_validate(
        request(generation_limits=limits(stdin_bytes=4096)).model_dump()
        | {"instruction": "Attributable diagnostic instruction. " * 200}
    )
    results = {}
    for fail_kind in [None, "generation-attempt", "generation-preflight", "generation-cost", "generation-status"]:
        name = "instruction_" + (fail_kind or "no_publication_failure")
        result = preflight_case(instruction_request, fail_kind)
        assert max(result["final_archive_bytes"].values()) < 4096
        if result["retained_recovery_payload_bytes"]:
            assert max(result["retained_recovery_payload_bytes"].values()) < 4096
        results[name] = result
    # A valid identifier can itself exceed the largest admitted stdin budget.
    # Keep the bulky identifier out of the diagnostic receipt; record only sizes.
    identifier_request = GenerationRequest.model_validate(
        request().model_dump() | {"request_id": "R" * 1_048_577}
    )
    for fail_kind in [None, "generation-preflight"]:
        name = "oversized_identifier_" + (fail_kind or "no_publication_failure")
        result = preflight_case(identifier_request, fail_kind)
        for archive_name in ("attempt", "preflight", "status"):
            assert result["final_archive_bytes"][archive_name] > identifier_request.limits.stdin_bytes
        if fail_kind is not None:
            assert result["retained_recovery_payload_bytes"]["attempt"] > identifier_request.limits.stdin_bytes
            assert result["retained_recovery_payload_bytes"]["preflight"] > identifier_request.limits.stdin_bytes
        results[name] = result
    return results


def main():
    product = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    result = {
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "scope": "unit_diagnostic; test doubles and real ArtifactStore; no subprocess/model/inference",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "reviewed_revision": REVISION,
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round2/reproduce-round2.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in product},
        "event_literals": event_literal_checks(),
        "structured_content": structured_content_checks(),
        "preflight": preflight_checks(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
