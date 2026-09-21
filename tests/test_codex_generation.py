"""Codex transport diagnostics. No network requests or real authoring."""

import json
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field

import pytest


def test_authoring_disables_unstable_feature_warning_events(tmp_path):
    from feature_rl.generation import CodexConfig
    command = CodexConfig().command('codex', tmp_path)
    assert 'suppress_unstable_features_warning=true' in command


def test_codex_usage_retains_reported_cache_write_and_reasoning_counts():
    from feature_rl.generation.models import GenerationUsage
    value = GenerationUsage.model_validate_json(
        '{"input_tokens":59905,"cached_input_tokens":0,"cache_write_input_tokens":0,'
        '"output_tokens":6132,"reasoning_output_tokens":875}')
    assert value.cache_write_input_tokens == 0
    assert value.reasoning_output_tokens == 875

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, StrictModel
from feature_rl.generation import (
    CodexConfig,
    CodexGenerationProvider,
    CodexUnavailable,
    GenerationProviderError,
)
from codex_fixtures import config, CodexRunner, events, response
from test_generation import request


def test_codex_is_the_only_authoring_configuration():
    import feature_rl.generation as generation

    assert hasattr(generation, "CodexConfig"), "Codex authoring boundary is missing"
    from pydantic import ValidationError

    assert generation.CodexConfig().model == "gpt-6-astra"
    with pytest.raises(ValidationError):
        generation.CodexConfig(model="local-model")
    with pytest.raises(ValidationError):
        generation.CodexConfig(model_directory="/tmp/model")


class Content(StrictModel):
    answer: int
    enabled: Literal[True]


def test_unsafe_remote_integer_bounds_keep_exact_local_validation():
    """Real Codex cannot emit even 1 under the int64-bounded schema; local bounds remain exact."""
    from jsonschema import Draft202012Validator
    from feature_rl.generation.schema import codex_request_schema, _parse_envelope

    class IntegerContent(StrictModel):
        value: Annotated[int, Field(ge=-(2**63), le=2**63-1)]
        small: Annotated[int, Field(ge=-10, le=10)]

    req = request()
    remote = codex_request_schema(req, IntegerContent)
    properties = remote['properties']['content']['properties']
    assert 'minimum' not in properties['value'] and 'maximum' not in properties['value']
    assert properties['small']['minimum'] == -10 and properties['small']['maximum'] == 10
    for number in (-(2**63), 0, 2**63-1):
        raw = response(req, {'value': number, 'small': 0})
        Draft202012Validator(remote).validate(json.loads(raw))
        assert _parse_envelope(raw, req, IntegerContent).value == number
    for number in (-(2**63)-1, 2**63, True, 1.0):
        with pytest.raises(ValueError):
            _parse_envelope(response(req, {'value': number, 'small': 0}), req, IntegerContent)
    with pytest.raises(ValueError):
        _parse_envelope(response(req, {'value': 0, 'small': 11}), req, IntegerContent)


def test_codex_schema_uses_runtime_and_contract_observable_enums():
    from feature_rl.generation.schema import codex_request_schema
    from feature_rl.requirements import RequirementContractProposal
    from feature_rl.scenarios import ScenarioPlanProposal
    from test_authoring import contract_request
    from test_authoring_policy import scenario_request

    contract = codex_request_schema(contract_request(), RequirementContractProposal)
    assert contract['$defs']['Requirement']['properties']['observable']['enum'] == [
        'CLI exit code', 'combined terminal output']
    assert contract['$defs']['Requirement']['properties']['requirement_id']['enum'] == list(
        contract_request().allowed_requirement_ids)
    scenario = codex_request_schema(scenario_request(), ScenarioPlanProposal)
    assert scenario['$defs']['Scenario']['properties']['observations']['items']['enum'] == [
        'combined terminal output', 'CLI exit code']
    assert scenario['$defs']['Scenario']['properties']['reset_needs']['maxItems'] == 0
    assert all(variant['properties']['source']['properties']['kind']['enum'] != ['RequirementContract']
        for variant in scenario['$defs']['EvidenceLink']['anyOf'])


def test_evidence_transport_binds_exact_admitted_identity_and_locator():
    from feature_rl.contracts import EvidenceLink
    from feature_rl.generation.schema import _parse_envelope

    class CitedContent(StrictModel):
        evidence: EvidenceLink

    req = request()
    context = req.contexts[0]
    evidence = dict(source=context.source.model_dump(mode='json'), locator=context.locator,
        quote=context.text, provenance_label=context.provenance_label)
    envelope = dict(response_id=req.response_id, source_ids=[context.context_id],
        requirement_ids=[], content={'evidence': evidence})
    assert _parse_envelope(json.dumps(envelope), req, CitedContent).evidence.source == context.source
    for invalid in (
        evidence | {'source': evidence['source'] | {'sha256': 'f' * 64}},
        evidence | {'locator': 'invented-location'},
    ):
        with pytest.raises(ValueError, match='strict schema'):
            _parse_envelope(json.dumps(envelope | {'content': {'evidence': invalid}}), req, CitedContent)


def test_strict_output_rejects_type_coercion_and_duplicate_keys():
    from feature_rl.generation.provider import _parse_envelope

    req = request()
    envelope = dict(
        response_id=req.response_id,
        source_ids=["SRC_1"],
        requirement_ids=["FEATURE_1"],
        content={"answer": 42, "enabled": True},
    )
    assert _parse_envelope(json.dumps(envelope), req, Content).answer == 42
    for content in (
        {"answer": "42", "enabled": True},
        {"answer": 42, "enabled": 1},
        {"answer": 42, "enabled": True, "extra": 1},
    ):
        with pytest.raises(ValueError):
            _parse_envelope(json.dumps(envelope | {"content": content}), req, Content)
    with pytest.raises(ValueError, match="JSON"):
        _parse_envelope('{"response_id":"a","response_id":"b"}', req, Content)


def provider_fixture(tmp_path, raw=None, archive=None):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    req = request()
    runner = CodexRunner(
        raw
        if raw is not None
        else events(response(req, {"answer": 42, "enabled": True}))
    )
    provider = CodexGenerationProvider(
        config=config(tmp_path), archive=archive or store.put_bytes, runner=runner
    )
    return store, req, runner, provider


def test_codex_invocation_uses_existing_auth_and_only_astra(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-must-not-be-inherited")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "existing-codex-home"))
    store, req, runner, provider = provider_fixture(tmp_path)
    result = provider.generate(req, Content)
    call = runner.calls[0]
    args = call["command"]
    assert args[args.index("--model") + 1] == "gpt-6-astra"
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert 'forced_login_method="chatgpt"' in args and 'model_provider="openai"' in args
    assert (
        "OPENAI_API_KEY" not in call["environment"]
        and "OPENAI_BASE_URL" not in call["environment"]
    )
    assert call["environment"]["CODEX_HOME"] == str(tmp_path / "existing-codex-home")
    assert result.content.answer == 42 and result.usage.input_tokens == 41
    assert result.cost.usd is None and result.cost.gpu_seconds is None
    from feature_rl.requirements.service import validate_recovered_generation

    validate_recovered_generation(store, req, Content, result)


def test_generation_retains_exact_transport_schema_and_process_boundary(tmp_path):
    from feature_rl.generation.schema import codex_request_schema
    store, req, runner, provider = provider_fixture(tmp_path)
    captured = []
    run = runner.run
    def observe(**kwargs):
        command = kwargs['command']
        captured.append(json.loads(Path(command[command.index('--output-schema')+1]).read_bytes()))
        return run(**kwargs)
    runner.run = observe
    result = provider.generate(req, Content)
    retained = json.loads(store.get_bytes(result.record.archives['response']))
    assert retained['transport_schema'] == captured[0] == codex_request_schema(req, Content)
    assert retained['process_boundary']['memory_samples'] == 5
    assert retained['process_boundary']['max_sampled_physical_footprint_bytes'] == 1000
    assert retained['process_boundary']['process_group_cleanup_verified'] is True
    assert retained['process_boundary']['monitoring_failures'] == 0


@pytest.mark.parametrize("text", ["Not logged in", "Logged in using an API key"])
def test_missing_chatgpt_auth_fails_before_generation(tmp_path, text):
    store, req, runner, provider = provider_fixture(tmp_path)
    executable = Path(provider._config.executable)
    executable.write_text("#!/usr/bin/env python3\nprint(" + repr(text) + ")\n")
    with pytest.raises(
        GenerationProviderError, match="ChatGPT authentication"
    ) as caught:
        provider.generate(req, Content)
    assert not runner.calls
    assert caught.value.record.publication_complete and not caught.value.record.success


def test_missing_cli_fails_clearly(tmp_path):
    with pytest.raises(CodexUnavailable, match="CLI is unavailable"):
        CodexConfig(executable=str(tmp_path / "absent")).verify(tmp_path)


def test_astra_unavailable_preserves_codex_error_without_retry(tmp_path):
    raw = (
        b'{"type":"error","message":"gpt-6-astra is not available for this account"}\n'
    )
    store, req, runner, provider = provider_fixture(tmp_path, raw)
    runner.exit_status = 1
    with pytest.raises(GenerationProviderError, match="not available for this account"):
        provider.generate(req, Content)
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not-json\n",
        events("{}"),
        events(response(request(), {"answer": "42", "enabled": True})),
        events(response(request(), {"answer": 42, "enabled": 1})),
        events(response(request(), {"answer": 42, "enabled": True})).replace(
            b'"output_tokens": 7', b'"output_tokens": true'
        ),
        events(response(request(), {"answer": 42, "enabled": True})).replace(
            b'"turn.completed"', b'"turn.failed"'
        ),
        events(response(request(), {"answer": 42, "enabled": True})).replace(
            b'"agent_message"', b'"command_execution"'
        ),
    ],
)
def test_invalid_codex_output_is_archived_and_never_retried(tmp_path, raw):
    store, req, runner, provider = provider_fixture(tmp_path, raw)
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(req, Content)
    assert len(runner.calls) == 1
    assert not caught.value.record.success and caught.value.record.publication_complete
    from feature_rl.requirements.service import validate_recovered_generation

    validate_recovered_generation(store, req, Content, caught.value)


@pytest.mark.parametrize("failure", ["response", "cost", "events", "status"])
def test_archive_recovery_preserves_validated_response_without_redispatch(
    tmp_path, failure
):
    store, req, runner, provider = provider_fixture(tmp_path)

    def unavailable(data, kind, visibility):
        if kind == "generation-" + failure:
            raise OSError("test storage outage")
        return store.put_bytes(data, kind, visibility)

    provider._archive = unavailable
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(req, Content)
    recovered = caught.value.replay_result(store.put_bytes)
    assert recovered.content.answer == 42 and len(runner.calls) == 1
    from feature_rl.requirements.service import validate_recovered_generation

    validate_recovered_generation(store, req, Content, recovered)


def test_transport_schema_preserves_actual_proposal_constraints():
    from feature_rl.generation.schema import output_envelope_schema, codex_output_schema
    from feature_rl.requirements import RequirementContractProposal
    from feature_rl.scenarios import ScenarioPlanProposal
    from feature_rl.verifiers import CheckerProposal, ControlProposal
    from jsonschema import Draft202012Validator

    for model in (
        RequirementContractProposal,
        ScenarioPlanProposal,
        CheckerProposal,
        ControlProposal,
    ):
        schema = codex_output_schema(output_envelope_schema(request(), model))
        Draft202012Validator.check_schema(schema)
        def check_refs(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    assert not (set(value) - {'$ref', 'description', 'title'})
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)
        check_refs(schema)
        assert schema["properties"]["content"]["additionalProperties"] is False
        assert set(schema["properties"]["content"]["required"]) == set(
            model.model_fields
        )


def test_numeric_literals_require_exact_json_types():
    from feature_rl.generation.schema import _parse_envelope

    class LiteralContent(StrictModel):
        value: Literal[1]

    with pytest.raises(ValueError):
        _parse_envelope(response(request(), {"value": 1.0}), request(), LiteralContent)


@pytest.mark.parametrize("defect", ["nonzero_exit", "tool"])
def test_failed_calls_retain_reported_token_overruns(tmp_path, defect):
    raw = events(
        response(request(), {"answer": 42, "enabled": True}), output_tokens=900
    )
    if defect == "tool":
        raw = raw.replace(b'"agent_message"', b'"command_execution"')
    store, req, runner, provider = provider_fixture(tmp_path, raw)
    if defect == "nonzero_exit":
        runner.exit_status = 1
    with pytest.raises(GenerationProviderError) as caught:
        provider.generate(req, Content)
    assert caught.value.cost.input_tokens == 41
    assert caught.value.cost.output_tokens == 900
    assert caught.value.record.success is False


def test_oversized_input_has_bounded_replayable_failure(tmp_path):
    from feature_rl.requirements.service import validate_recovered_generation

    store, req, runner, provider = provider_fixture(tmp_path)
    req = req.model_copy(update={"instruction": "x" * (req.limits.stdin_bytes + 1)})
    with pytest.raises(GenerationProviderError, match="input byte cap") as caught:
        provider.generate(req, Content)
    assert not runner.calls
    assert set(caught.value.record.archives) == {"attempt", "cost", "status"}
    validate_recovered_generation(store, req, Content, caught.value)
