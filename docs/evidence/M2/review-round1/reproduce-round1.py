"""Independent narrow protocol/recovery checks. No child, model, or inference.

Uses the clearly scoped backend/runner doubles from tests/test_generation.py.
The preserved previous review files are not imported or changed.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, StrictModel, UTCDateTime, Visibility
from feature_rl.generation import GenerationProviderError, GenerationRequest, LocalGenerationProvider
from test_generation import FakeBackend, FakeRunner, SmokeContent, limits, request, worker_events


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


class StructuredContent(StrictModel):
    values: tuple[str, ...]
    visibility: Visibility
    recorded_at: UTCDateTime


def fixture():
    return [json.loads(line) for line in worker_events().splitlines()]


def invoke(events, schema=SmokeContent, generation_request=None):
    runner = FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    with tempfile.TemporaryDirectory(prefix="protocol-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)
        provider = LocalGenerationProvider(backend=FakeBackend(), runner=runner, archive=store.put_bytes)
        try:
            response = provider.generate(generation_request or request(), schema)
            record = response.record
            result = {"accepted": True, "content": response.content.model_dump(mode="json")}
        except GenerationProviderError as error:
            record = error.record
            result = {"accepted": False, "error": str(error)}
        result.update({
            "record_success": record.success,
            "usage": json.loads(store.get_bytes(record.archives["usage"])),
            "synthetic_runner_calls": len(runner.calls),
        })
        return result


def original_cases():
    results = {}
    events = fixture()
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = {"values": ["valid"], "visibility": "authoring", "recorded_at": "2026-09-19T11:00:00Z"}
    events[-1]["output_text"] = json.dumps(envelope)
    results["F2_strict_json_transport"] = invoke(events, StructuredContent)
    assert results["F2_strict_json_transport"]["accepted"]
    for name, index, field, value in [
        ("wrong_config_hash", 0, "config_sha256", "0" * 64),
        ("wrong_tokenizer_hash", 0, "tokenizer_sha256", "0" * 64),
        ("wrong_weight_hash", 0, "weights_sha256", "0" * 64),
        ("wrong_dependency", 0, "dependency_versions", {"mlx": "0.0.0"}),
        ("remote_code", 0, "remote_code", True),
        ("nonlocal", 0, "local_files_only", False),
        ("extra_field", 0, "unexpected", "forbidden"),
        ("wrong_completion_id", -1, "response_id", "DIFFERENT_RESPONSE"),
        ("positive_logprob", -2, "selected_model_logprob", 2.0),
    ]:
        events = fixture()
        events[index][field] = value
        result = invoke(events)
        assert not result["accepted"]
        results["F3_" + name] = {"accepted": result["accepted"], "error": result["error"]}
    events = fixture()
    del events[0]["config_sha256"]
    result = invoke(events)
    assert not result["accepted"]
    results["F3_missing_field"] = {"accepted": result["accepted"], "error": result["error"]}
    events = fixture()
    envelope = json.loads(events[-1]["output_text"])
    envelope["requirement_ids"] = ["UNKNOWN_REQUIREMENT"]
    events[-1]["output_text"] = json.dumps(envelope)
    results["F5_failed_envelope_usage"] = invoke(events)
    assert not results["F5_failed_envelope_usage"]["accepted"]
    assert results["F5_failed_envelope_usage"]["usage"]["accepted_response_usage"] is False
    return results


def literal_coercion():
    results = {}
    for name, index, field, value in [
        ("integer_local_files_only", 0, "local_files_only", 1),
        ("integer_remote_code", 0, "remote_code", 0),
        ("float_protocol_version", 0, "protocol_version", 3.0),
        ("integer_fresh_process", 3, "fresh_process", 1),
        ("integer_fresh_prompt_cache", -1, "fresh_prompt_cache", 1),
    ]:
        events = fixture()
        events[index][field] = value
        result = invoke(events)
        assert result["accepted"]
        results[name] = {
            "field": field, "supplied_value": value, "supplied_type": type(value).__name__,
            "provider_accepted": result["accepted"], "record_success": result["record_success"],
            "usage_accepted": result["usage"]["accepted_response_usage"],
        }
    return results


def recovery():
    runner = FakeRunner()
    with tempfile.TemporaryDirectory(prefix="recovery-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)

        def failed_response(data, kind, visibility):
            if kind == "generation-response":
                raise OSError("reviewer injected response publication failure")
            return store.put_bytes(data, kind, visibility)

        provider = LocalGenerationProvider(backend=FakeBackend(), runner=runner, archive=failed_response)
        try:
            provider.generate(request(), SmokeContent)
        except GenerationProviderError as error:
            before = {
                "generation_succeeded": error.record.generation_succeeded,
                "publication_complete": error.record.publication_complete,
                "cost": error.cost.model_dump(mode="json"),
                "response_wall_seconds": error.response["wall_seconds"],
                "published": sorted(error.record.archives),
            }
            recovered = error.replay_publication(store.put_bytes)
            assert recovered.success and recovered.publication_complete
            assert len(runner.calls) == 1
            for ref in recovered.archives.values():
                store.get_bytes(ref)
            return {
                "before_replay": before,
                "after_replay_success": recovered.success,
                "after_replay_archive_kinds": sorted(recovered.archives),
                "all_recovered_refs_resolved": True,
                "synthetic_runner_calls": len(runner.calls),
            }
        raise AssertionError("expected injected publication failure")


def unarchived_preflight_rejection():
    runner = FakeRunner()
    archive_calls = []
    generation_request = GenerationRequest.model_validate(
        request(generation_limits=limits(stdin_bytes=4096)).model_dump()
        | {"instruction": "Attributable diagnostic instruction. " * 200}
    )
    with tempfile.TemporaryDirectory(prefix="input-limit-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)

        def archive(data, kind, visibility):
            archive_calls.append(kind)
            return store.put_bytes(data, kind, visibility)

        provider = LocalGenerationProvider(backend=FakeBackend(), runner=runner, archive=archive)
        try:
            provider.generate(generation_request, SmokeContent)
        except ValueError as error:
            result = {
                "request_passed_GenerationRequest_validation": True,
                "stdin_cap_bytes": generation_request.limits.stdin_bytes,
                "request_json_bytes": len(generation_request.model_dump_json().encode()),
                "exception_type": type(error).__name__,
                "exception_message": str(error),
                "has_record": hasattr(error, "record"),
                "archive_calls": archive_calls,
                "synthetic_runner_calls": len(runner.calls),
            }
            assert not result["has_record"] and not archive_calls and not runner.calls
            return result
        raise AssertionError("expected untyped preflight rejection")


def main():
    product = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    result = {
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "scope": "unit_diagnostic; test doubles only; no subprocess/model/inference",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_revision": "7239379ebf6921fd816b1bded9bb07a685b84c1a",
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round1/reproduce-round1.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in product},
        "original_cases": original_cases(),
        "F4_publication_recovery": recovery(),
        "remaining_F3_literal_coercion": literal_coercion(),
        "new_unarchived_preflight_rejection": unarchived_preflight_rejection(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
