"""Independent round-5 provider boundary checks; no worker/model/native execution.

Only trusted unit schemas, explicit backend/runner doubles, and temporary real
M0 storage are used. Unit event/token/cost fixtures are not empirical evidence.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Literal

from pydantic import Field, ValidationError, ValidationInfo, field_serializer, field_validator, model_validator

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.generation import GenerationProviderError, LocalGenerationProvider
from feature_rl.generation.provider import ARCHIVE_KINDS, _build_literal_prevalidator
import test_generation as t
from test_contracts_examples import examples

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REVISION = "e14f67983db678813afcfeb191532e691b0b20e3"
LIFECYCLE = []


def inventory(directory):
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.glob("*.py"))}


class CountingBackend(t.FakeBackend):
    def __init__(self):
        self.verify_calls = 0

    def verify(self):
        self.verify_calls += 1
        return super().verify()


def invoke(schema, raw, expected, *, fail_publication=False):
    events = [json.loads(line) for line in t.worker_events().splitlines()]
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = raw
    events[-1]["output_text"] = json.dumps(envelope)
    runner = t.FakeRunner(stdout=("\n".join(json.dumps(event) for event in events) + "\n").encode())
    backend = CountingBackend()
    failed = False
    content = None
    with tempfile.TemporaryDirectory(prefix="provider-store-", dir=HERE) as directory:
        store = ArtifactStore(Path(directory) / "objects", c.ActorRole.AUTHOR)

        def publish(data, kind, visibility):
            nonlocal failed
            if fail_publication and kind == "generation-schema" and not failed:
                failed = True
                raise OSError("reviewer injected one schema-publication failure")
            return store.put_bytes(data, kind, visibility)

        try:
            result = LocalGenerationProvider(backend=backend, runner=runner, archive=publish).generate(t.request(), schema)
            content, record = result.content, result.record
            assert expected == "success"
        except GenerationProviderError as error:
            record = error.record
            if fail_publication:
                assert failed and error.recovery is not None
                assert not record.publication_complete
                assert record.error_code == "ArchivePublicationError"
                record = error.replay_publication(store.put_bytes)
            assert record.error_code == expected, record
        assert set(record.archives) == set(ARCHIVE_KINDS)
        resolved = {name: store.get_bytes(ref) for name, ref in record.archives.items()}
        assert record.publication_complete
        success = expected == "success"
        assert record.success is success and record.generation_succeeded is success
        usage = json.loads(resolved["usage"])
        assert usage["accepted_response_usage"] is success
        calls = 0 if expected == "GenerationSchemaUnsupportedError" else 1
        assert backend.verify_calls == len(runner.calls) == calls
        receipt = {"schema": schema.__name__, "raw_content": raw,
                   "error_code": record.error_code, "success": record.success,
                   "generation_succeeded": record.generation_succeeded,
                   "publication_complete": record.publication_complete,
                   "publication_replayed": fail_publication,
                   "accepted_response_usage": usage["accepted_response_usage"],
                   "backend_double_calls": backend.verify_calls,
                   "runner_double_calls": len(runner.calls),
                   "resolved_archive_count": len(resolved),
                   "resolved_archive_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in resolved.items()},
                   "archive_payload_bytes": sum(map(len, resolved.values()))}
        return content, receipt


def baseline(schema, raw, accepted=True):
    try:
        result = schema.model_validate_json(canonical_json(raw))
    except ValidationError as error:
        assert not accepted
        return {"ordinary_JSON_accepted": False, "ordinary_JSON_error": str(error)}
    assert accepted
    return {"ordinary_JSON_accepted": True, "ordinary_JSON_content": result.model_dump(mode="json")}


class StringLifecycle(c.StrictModel):
    flag: Literal["ready"]
    number: int
    marker: str = Field(default_factory=lambda: LIFECYCLE.append("default") or "generated")

    @field_validator("number", mode="before")
    @classmethod
    def normalize(cls, value):
        LIFECYCLE.append("before")
        return int(value)

    def model_post_init(self, _context):
        LIFECYCLE.append("post_init")

    @model_validator(mode="after")
    def after(self, info: ValidationInfo):
        LIFECYCLE.append("after:" + info.mode)
        assert info.mode == "json"
        return self


class NumericSerialization(c.StrictModel):
    flag: Literal[True]

    @field_serializer("flag")
    def serialize(self, value):
        return value


class MultipleBoolInt(c.StrictModel):
    flag: Literal[True, 1]


class MultipleIntFloat(c.StrictModel):
    value: Literal[3, 3.0]


class RecursiveLiteral(c.StrictModel):
    flag: Literal[True]
    children: list["RecursiveLiteral"]


def main():
    started = datetime.now(timezone.utc).isoformat()
    store_before = inventory(ROOT / "src/feature_rl/artifacts")
    store_status_before = subprocess.check_output(["git", "status", "--short", "--", "src/feature_rl/artifacts"], cwd=ROOT, text=True)
    results = {}

    for schema, raw in (
        (t.AfterChainNumericLiteralContent, {"flag": True, "number": "3"}),
        (t.OmitNumericLiteralContent, {"flag": True, "values": ["skip", "keep"]}),
    ):
        observed = baseline(schema, raw)
        t.OMIT_CALLBACKS.clear()
        _, receipt = invoke(schema, raw, "GenerationSchemaUnsupportedError")
        assert t.OMIT_CALLBACKS == []
        results[schema.__name__] = observed | receipt | {"provider_omit_callbacks": []}

    for schema, raw, field, expected in (
        (t.AfterChainStringLiteralContent, {"flag": "ready", "number": "3"}, "number", 3),
        (t.OmitStringLiteralContent, {"flag": "ready", "values": ["skip", "keep"]}, "values", ["keep"]),
    ):
        observed = baseline(schema, raw)
        assert _build_literal_prevalidator(schema) is None
        t.OMIT_CALLBACKS.clear()
        content, receipt = invoke(schema, raw, "success")
        assert getattr(content, field) == expected
        if schema is t.OmitStringLiteralContent:
            assert t.OMIT_CALLBACKS == ["skip", "keep"]
        results[schema.__name__] = observed | receipt | {"provider_omit_callbacks": list(t.OMIT_CALLBACKS)}

    for schema in (t.GuardedModelSetContent, t.GuardedModelFrozenSetContent,
                   t.NestedGuardedModelSetContent, t.NestedGuardedModelFrozenSetContent):
        for equal in (False, True):
            raw = {"values": [{"flag": True, "label": "first"},
                              {"flag": True, "label": "first" if equal else "second"}]}
            if schema.__name__.startswith("Nested"):
                raw = {"payload": raw}
            observed = baseline(schema, raw, accepted=not equal)
            _, receipt = invoke(schema, raw, "GenerationSchemaUnsupportedError")
            results[schema.__name__ + ("_equal" if equal else "_unequal")] = observed | receipt

    for schema, raw in (
        (t.UnsupportedDecimalLiteralContent, {"value": 1}),
        (t.ExtraAllowRootContent, {"flag": True, "x": 1, "y": 2}),
        (t.ExtraAllowNestedContent, {"flag": True, "item": {"x": 1, "y": 2}}),
        (t.NumericLiteralDefaultContent, {"flag": True}),
        (t.NumericKeyLiteralMappingContent, {"flags": {"1": True}}),
        (NumericSerialization, {"flag": True}),
    ):
        observed = baseline(schema, raw)
        _, receipt = invoke(schema, raw, "GenerationSchemaUnsupportedError")
        results[schema.__name__] = observed | receipt

    _, results["unsupported_schema_publication_recovery"] = invoke(
        t.OmitNumericLiteralContent, {"flag": True, "values": ["skip", "keep"]},
        "GenerationSchemaUnsupportedError", fail_publication=True)

    raw = {"flag": "ready", "number": "3"}
    LIFECYCLE.clear()
    observed = baseline(StringLifecycle, raw)
    expected_lifecycle = ["before", "default", "post_init", "after:json"]
    assert LIFECYCLE == expected_lifecycle
    LIFECYCLE.clear()
    content, receipt = invoke(StringLifecycle, raw, "success")
    assert content.number == 3 and content.marker == "generated"
    assert LIFECYCLE == expected_lifecycle
    results["StringLifecycle"] = observed | receipt | {"lifecycle": list(LIFECYCLE)}

    for schema, raw, field, expected_type in (
        (t.ScalarBooleanUnionContent, {"flag": 1}, "flag", int),
        (t.ScalarBooleanUnionContent, {"flag": True}, "flag", bool),
        (t.ScalarIntegerUnionContent, {"value": 3.0}, "value", float),
        (t.ScalarIntegerUnionContent, {"value": 3}, "value", int),
        (MultipleBoolInt, {"flag": 1}, "flag", int),
        (MultipleBoolInt, {"flag": True}, "flag", bool),
        (MultipleIntFloat, {"value": 3.0}, "value", float),
        (t.MixedLiteralContent, {"mode": {"flag": 1}}, "mode", t.NumberAlternative),
    ):
        content, receipt = invoke(schema, raw, "success")
        assert type(getattr(content, field)) is expected_type
        results[schema.__name__ + "_" + expected_type.__name__] = receipt | {
            "returned_type": type(getattr(content, field)).__name__,
            "returned_content": content.model_dump(mode="json")}

    for schema, raw, expected in (
        (t.UnionLiteralContent, {"mode": {"flag": True}}, "success"),
        (t.UnionLiteralContent, {"mode": {"flag": False}}, "success"),
        (t.UnionLiteralContent, {"mode": {"flag": 1}}, "ValueError"),
        (t.PatternedLiteralContent, {"flags": {"k_one": True}}, "success"),
        (t.PatternedLiteralContent, {"flags": {"k_one": 1}}, "ValueError"),
        (t.PatternedLiteralContent, {"flags": {"bad": True}}, "ValueError"),
        (t.NestedScalarUnionContent, {"values": [1, True]}, "success"),
        (RecursiveLiteral, {"flag": True, "children": [{"flag": True, "children": []}]}, "success"),
        (RecursiveLiteral, {"flag": True, "children": [{"flag": 1, "children": []}]}, "ValueError"),
        (t.ExtraIgnoreNestedContent, {"flag": True, "item": {"x": 1, "y": 2}}, "success"),
        (t.ExtraForbidNestedContent, {"flag": True, "item": {"x": 1}}, "success"),
        (t.ExtraForbidNestedContent, {"flag": True, "item": {"x": 1, "y": 2}}, "ValueError"),
    ):
        content, receipt = invoke(schema, raw, expected)
        if content is not None:
            receipt["returned_content"] = content.model_dump(mode="json")
        if schema is t.NestedScalarUnionContent:
            assert tuple(type(value) for value in content.values) == (int, bool)
        if schema is RecursiveLiteral and content is not None:
            assert type(content.children[0]) is RecursiveLiteral
        if schema is t.ExtraIgnoreNestedContent:
            assert content.item.model_dump(mode="json") == {"x": 1}
        results["structural_" + str(len(results)) + "_" + schema.__name__] = receipt

    for schema in (c.RequirementContract, c.ScenarioPlan):
        raw = examples()[schema.__name__]
        observed = baseline(schema, raw)
        assert _build_literal_prevalidator(schema) is None
        content, receipt = invoke(schema, raw, "success")
        assert type(content) is schema and content.model_dump(mode="json") == raw
        results["synthetic_fixture_transport_" + schema.__name__] = observed | receipt

    raw = {"visibility": "authoring", "values": ["alpha", "beta"], "recorded_at": "2026-09-19T10:00:00Z"}
    content, receipt = invoke(t.EnumLiteralContent, raw, "success")
    assert content.visibility is c.Visibility.AUTHORING and content.values == ("alpha", "beta")
    assert content.recorded_at == datetime(2026, 9, 19, 10, tzinfo=timezone.utc)
    results["string_enum_tuple_UTC"] = receipt
    _, results["non_UTC_rejection"] = invoke(t.EnumLiteralContent, raw | {"recorded_at": "2026-09-19T10:00:00+01:00"}, "ValueError")

    store_after = inventory(ROOT / "src/feature_rl/artifacts")
    assert store_before == store_after, "M0 source changed during diagnostic; rerun needs a fresh evidence filename"
    print(json.dumps({"producer": "independent M2 reviewer /root/review_m2_provider",
                      "scope": "trusted schema/provider doubles and real temporary ArtifactStore only; fixture tasks/tokens/costs are not qualification evidence",
                      "reviewed_revision": REVISION, "started_at_UTC": started,
                      "finished_at_UTC": datetime.now(timezone.utc).isoformat(),
                      "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", str(Path(__file__).relative_to(ROOT))],
                      "generation_source_sha256": inventory(ROOT / "src/feature_rl/generation"),
                      "ArtifactStore_source_sha256_before": store_before,
                      "ArtifactStore_source_sha256_after": store_after,
                      "ArtifactStore_git_status_before": store_status_before,
                      "ArtifactStore_scope_note": "M0 is independently adding optional bounded reads; these checks use default store reads only and do not approve that extension",
                      "native_inference_calls": 0, "model_loads": 0,
                      "case_count": len(results), "cases": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
