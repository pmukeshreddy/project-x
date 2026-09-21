from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import io
import tarfile
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import (
    ActorRole,
    AllowedChanges,
    ArtifactRef,
    CostRecord,
    EvidenceRecord,
    Provenance,
    ResourceLimits,
    SeedPolicy,
    Visibility,
)
from feature_rl.requirements import (
    AuthoringExhausted,
    AuthoringEvidenceResolver,
    AuthoringJournalPublicationPending,
    AuthoringPublicationPending,
    ContractAuthoringService,
    ContractFinalizationInputs,
    ContractFinalizer,
    RuntimeDiscoveryService,
    GenerationCandidate,
    GroundedSource,
    GroundingError,
    RequirementContractProposal,
    BaselineRetriever,
    RetrievalPolicy,
    RetrievalRequest,
    RetrievalRejected,
    EvidenceResolutionError,
    build_contract_request,
)
from feature_rl.generation import (
    GenerationCallRecord,
    GenerationLimits,
    CodexGenerationProvider,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)
from feature_rl.generation.provider import GenerationProviderError


def ref(kind: str, value: str, visibility: Visibility = Visibility.AUTHORING) -> ArtifactRef:
    return ArtifactRef(
        sha256=value * 64,
        kind=kind,
        schema_version=1,
        visibility=visibility,
        encoding="json" if kind in {"RequirementContract", "ScenarioPlan"} else "bytes",
    )


REQUEST = ref("authoring-request", "a")
BASELINE = ref("source-archive", "b")
PUBLIC_CHECK = ref("public-check", "c", Visibility.PUBLIC)
RECEIPT = ref("discovery-receipt", "d")
DISCOVERY = ref("runtime-discovery", "9")
RECIPE = ref("EnvironmentRecipe", "6")
PREPARED = SimpleNamespace(recipe=RECIPE)


def cost(category: str = "authoring") -> CostRecord:
    return CostRecord(
        category=category,
        wall_seconds=1.0,
        cpu_seconds=0.5,
        gpu_seconds=None,
        input_tokens=10,
        output_tokens=5,
        human_minutes=None,
        usd=None,
        measurement="partial",
        note="Measured diagnostic fields; other cost components are unknown.",
    )


def provenance(*inputs: ArtifactRef) -> Provenance:
    evidence = EvidenceRecord(
        producer="tests",
        command=("pytest",),
        recorded_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        exit_status=0,
        artifacts=(RECEIPT,),
        revision="1" * 40,
        scope="unit_diagnostic",
    )
    return Provenance(
        producer="feature_rl.requirements",
        producer_version="1" * 40,
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        inputs=inputs,
        evidence=(evidence,),
    )


def limits() -> ResourceLimits:
    return ResourceLimits(
        wall_seconds=120,
        cpu_seconds=60,
        memory_bytes=512 * 1024 * 1024,
        pids=64,
        output_bytes=2 * 1024 * 1024,
        disk_bytes=128 * 1024 * 1024,
        tool_calls=100,
        input_tokens=16_384,
        output_tokens=4_096,
    )


def allowed() -> AllowedChanges:
    return AllowedChanges(
        source_roots=("src",),
        forbidden_paths=("tests",),
        dependencies="forbidden",
        dependency_artifacts=(),
        additional_artifact_types=(),
    )


def bound_discovery_text(
    *, baseline: ArtifactRef = BASELINE, recipe: ArtifactRef = RECIPE
) -> str:
    value = json.loads(discovery_stdout())
    from m4_fixtures import runtime_policy
    value.update(version="runtime-discovery-v1", profile_sha256=hashlib.sha256(canonical_json(runtime_policy().profile.model_dump(mode="json"))).hexdigest())
    value.update(
        {
            "baseline": baseline.model_dump(mode="json"),
            "recipe": recipe.model_dump(mode="json"),
            "build_evidence_sha256": "7" * 64,
            "execution_evidence_sha256": "8" * 64,
        }
    )
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sources() -> tuple[GroundedSource, ...]:
    return (
        GroundedSource(
            context_id="REQUEST",
            role="request",
            source=REQUEST,
            locator="request:1-4",
            text="Unknown commands should offer a close command suggestion.",
            provenance_label="reconstructed_specification",
        ),
        GroundedSource(
            context_id="BASELINE",
            role="baseline",
            source=BASELINE,
            locator="src/click/core.py:1-20",
            text="class Group: public command dispatch entry point",
            provenance_label="existing_obligation",
        ),
        GroundedSource(
            context_id="M3_RUNTIME_DISCOVERY",
            role="baseline",
            source=DISCOVERY,
            locator="m3:runtime-discovery-v1",
            text=bound_discovery_text(),
            provenance_label="existing_obligation",
        ),
    )


def contract_proposal(**updates) -> RequirementContractProposal:
    values = {
        "capability": "Suggest close command names for unknown commands.",
        "entry_points": ("click.Group",),
        "requirements": (
            {
                "requirement_id": "FEATURE_COMMAND_SUGGESTION",
                "statement": "An unknown command with a close match offers a suggestion.",
                "mandatory": True,
                "evidence": (
                    {
                        "source": REQUEST,
                        "locator": "request:1-4",
                        "quote": "offer a close command suggestion",
                        "provenance_label": "reconstructed_specification",
                    },
                ),
                "observable": "combined terminal output",
            },
        ),
        "compatibility_obligations": (
            {
                "requirement_id": "COMPAT_GROUP_DISPATCH",
                "statement": "Existing group command dispatch remains available.",
                "mandatory": True,
                "evidence": (
                    {
                        "source": BASELINE,
                        "locator": "src/click/core.py:1-20",
                        "quote": "public command dispatch entry point",
                        "provenance_label": "existing_obligation",
                    },
                ),
                "observable": "CLI exit code",
            },
        ),
        "ambiguities": (
            {
                "question": "Is exact wording fixed?",
                "resolution": "No exact wording is required.",
                "disposition": "excluded",
                "evidence": (
                    {
                        "source": REQUEST,
                        "locator": "request:1-4",
                        "quote": "close command suggestion",
                        "provenance_label": "reconstructed_specification",
                    },
                ),
            },
        ),
        "feature_files": ({
            "path": "src/click/core.py", "requirement_ids": ("FEATURE_COMMAND_SUGGESTION",),
            "rationale": "Implement the requested suggestion at the group dispatch entry point.",
            "evidence": ({"source": BASELINE, "locator": "src/click/core.py:1-20",
                          "quote": "public command dispatch entry point", "provenance_label": "existing_obligation"},),
        },),
        "allowed_changes": allowed(),
    }
    return RequirementContractProposal.model_validate(values | updates)


def contract_inputs() -> ContractFinalizationInputs:
    return ContractFinalizationInputs(
        visible_request="Unknown commands should offer a close command suggestion.",
        allowed_requirement_ids=(
            "FEATURE_COMMAND_SUGGESTION",
            "COMPAT_GROUP_DISPATCH",
            "UNUSED_POOL_ID",
        ),
        entry_points=("click.Group",),
        supported_observables=("combined terminal output", "CLI exit code"),
        runtime_discovery=DISCOVERY,
        allowed_changes=allowed(),
        public_checks=(),
        episode_limits=limits(),
        provenance_label="reconstructed_specification",
        visibility=Visibility.AUTHORING,
        provenance=provenance(REQUEST, BASELINE, DISCOVERY, RECEIPT),
        costs=(cost(),),
    )


def finalized_contract():
    return ContractFinalizer().finalize(contract_proposal(), contract_inputs(), sources())


def test_contract_proposal_preserves_m0_constraints_without_envelope_fields():
    assert set(RequirementContractProposal.model_fields) == {
        "capability",
        "entry_points",
        "requirements",
        "compatibility_obligations",
        "feature_files",
        "ambiguities",
        "allowed_changes",
    }
    with pytest.raises(ValidationError):
        contract_proposal(entry_points=())
    with pytest.raises(ValidationError):
        contract_proposal(requirements=())
    with pytest.raises(ValidationError):
        RequirementContractProposal.model_validate(
            contract_proposal().model_dump() | {"visibility": "authoring"}
        )


def test_contract_finalizer_runs_grounding_joins_and_real_m0_validation():
    contract = finalized_contract()
    assert contract.kind == "RequirementContract"
    assert contract.visible_request == contract_inputs().visible_request
    assert contract.requirements[0].requirement_id == "FEATURE_COMMAND_SUGGESTION"
    assert "UNUSED_POOL_ID" not in {
        requirement.requirement_id
        for requirement in contract.requirements + contract.compatibility_obligations
    }
    assert contract.provenance.inputs == (REQUEST, BASELINE, DISCOVERY, RECEIPT)


def test_contract_finalizer_requires_exact_validated_runtime_discovery_context():
    without_runtime = tuple(source for source in sources() if source.source != DISCOVERY)
    with pytest.raises(GroundingError, match="runtime discovery"):
        ContractFinalizer().finalize(contract_proposal(), contract_inputs(), without_runtime)


@pytest.mark.parametrize(
    "update",
    (
        {"visible_request": "Different request."},
        {"provenance_label": "historical_request"},
    ),
)
def test_contract_finalizer_binds_visible_request_and_provenance_to_resolved_source(update):
    with pytest.raises(GroundingError, match="resolved authoring evidence"):
        ContractFinalizer().finalize(
            contract_proposal(), contract_inputs().model_copy(update=update), sources()
        )


@pytest.mark.parametrize(
    "proposal, expected",
    [
        (
            lambda: contract_proposal(
                requirements=(
                    contract_proposal().requirements[0].model_copy(
                        update={
                            "evidence": (
                                contract_proposal().requirements[0].evidence[0].model_copy(
                                    update={"quote": "forged quote"}
                                ),
                            )
                        }
                    ),
                )
            ),
            "quote",
        ),
        (
            lambda: contract_proposal(
                requirements=(
                    contract_proposal().requirements[0].model_copy(
                        update={
                            "evidence": (
                                contract_proposal().requirements[0].evidence[0].model_copy(
                                    update={"source": ref("authoring-request", "e")}
                                ),
                            )
                        }
                    ),
                )
            ),
            "allowlist",
        ),
        (
            lambda: contract_proposal(
                requirements=(
                    contract_proposal().requirements[0].model_copy(
                        update={"observable": "private implementation state"}
                    ),
                )
            ),
            "observable",
        ),
        (
            lambda: contract_proposal(
                ambiguities=(
                    contract_proposal().ambiguities[0].model_copy(
                        update={"disposition": "unresolved", "resolution": None}
                    ),
                )
            ),
            "unresolved",
        ),
    ],
)
def test_contract_finalizer_rejects_ungrounded_or_unsupported_content(proposal, expected):
    with pytest.raises(GroundingError, match=expected):
        ContractFinalizer().finalize(proposal(), contract_inputs(), sources())














def generation_limits() -> GenerationLimits:
    return GenerationLimits(

        wall_seconds=120,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        input_tokens=8_192,
        output_tokens=4_096,
        physical_footprint_kill_bytes=4_294_967_296,
        physical_footprint_poll_seconds=0.02,
        declared_memory_ceiling_bytes=5_368_709_120,
    )


def provider_result(content, index: int = 1) -> GenerationResult:
    archive = ref("generation-status", str(index))
    return GenerationResult(
        content=content,
        usage=GenerationUsage(
            input_tokens=2,
            cached_input_tokens=0,
            output_tokens=1,
        ),
        cost=cost(),
        record=GenerationCallRecord(
            attempt_id=f"attempt-{index}",
            recorded_at=datetime(2026, 9, 19, 1, index, tzinfo=timezone.utc),
            request_id=f"REQ_{index}",
            response_id=f"RESP_{index}",
            success=True,
            generation_succeeded=True,
            publication_complete=True,
            error_code=None,
            archives={"status": archive},
        ),
    )


def archived_provider_result(
    store, request, content, index: int = 1, *, response_payload=None
) -> GenerationResult:
    """Publish the provider archive bindings required to resume one successful result."""
    from codex_fixtures import events
    base = provider_result(content, index)
    visibility = (
        Visibility.AUTHORING
        if request.stage is GenerationStage.INITIAL_AUTHORING
        else Visibility.PRIVATE
    )
    request_payload = request.model_dump(mode="json")
    schema = (
        RequirementContractProposal
        if request.stage is GenerationStage.INITIAL_AUTHORING
        else type(content)
    )
    schema_payload = schema.model_json_schema()
    envelope = {
        "response_id": request.response_id,
        "source_ids": [item.context_id for item in request.contexts],
        "requirement_ids": list(request.allowed_requirement_ids),
        "content": schema.model_validate(content).model_dump(mode="json"),
    }
    payloads = {
        "attempt": {
            "producer": "feature_rl.generation.CodexGenerationProvider",
            "protocol_version": "codex-exec-v1",
            "configured_model_id": "gpt-6-astra",
            "reasoning_effort": "high",
            "attempt_id": base.record.attempt_id,
            "recorded_at": base.record.recorded_at.isoformat(),
            "request_id": request.request_id,
            "response_id": request.response_id,
            "prompt_id": request.prompt_id,
            "request_sha256": hashlib.sha256(canonical_json(request_payload)).hexdigest(),
            "output_schema_sha256": hashlib.sha256(canonical_json(schema_payload)).hexdigest(),
        },
        "request": request_payload,
        "response": {"output_text": json.dumps(envelope)} if response_payload is None else {**response_payload, "output_text": json.dumps(envelope)},
        "retrieval": {"contexts": [item.model_dump(mode="json") for item in request.contexts]},
        "schema": schema_payload,
        "options": {"model": "gpt-6-astra", "limits": request.limits.model_dump(mode="json")},
        "provenance": {"observed": True},
        "usage": {"accepted_response_usage": True, "accepted": base.usage.model_dump(mode="json")},
        "cost": base.cost.model_dump(mode="json"),
        "events": events(json.dumps(envelope), input_tokens=2, output_tokens=1),
    }
    refs = {}
    for name, payload in payloads.items():
        raw = payload if isinstance(payload, bytes) else canonical_json(payload)
        refs[name] = store.put_bytes(raw, f"generation-{name}", visibility)
    status = {
        "attempt_id": base.record.attempt_id,
        "recorded_at": base.record.recorded_at.isoformat(),
        "request_id": request.request_id,
        "response_id": request.response_id,
        "success": True,
        "generation_succeeded": True,
        "publication_complete": True,
        "publication_recovered": True,
        "error_type": None,
        "error": None,
        "archive_refs": {name: ref.model_dump(mode="json") for name, ref in refs.items()},
    }
    refs["status"] = store.put_bytes(canonical_json(status), "generation-status", visibility)
    return base.model_copy(
        update={
            "record": base.record.model_copy(
                update={
                    "request_id": request.request_id,
                    "response_id": request.response_id,
                    "archives": refs,
                }
            )
        }
    )


def archived_provider_error(store, request, content, index: int = 1) -> GenerationProviderError:
    successful = archived_provider_result(store, request, content, index)
    visibility = successful.record.archives["usage"].visibility
    refs = dict(successful.record.archives)
    refs["usage"] = store.put_bytes(
        canonical_json({"accepted_response_usage": False, "accepted": None, "observed": {}}),
        "generation-usage",
        visibility,
    )
    record = successful.record.model_copy(
        update={
            "success": False,
            "generation_succeeded": False,
            "error_code": "ValueError",
            "archives": refs,
        }
    )
    status = {
        "attempt_id": record.attempt_id,
        "recorded_at": record.recorded_at.isoformat(),
        "request_id": record.request_id,
        "response_id": record.response_id,
        "success": False,
        "generation_succeeded": False,
        "publication_complete": True,
        "publication_recovered": True,
        "error_type": "ValueError",
        "error": "malformed output",
        "archive_refs": {
            name: ref.model_dump(mode="json") for name, ref in refs.items() if name != "status"
        },
    }
    refs["status"] = store.put_bytes(canonical_json(status), "generation-status", visibility)
    record = record.model_copy(update={"archives": refs})
    return GenerationProviderError(
        "malformed output", record, cost=successful.cost, usage_observation={}
    )


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate(self, request, output_schema):
        self.calls.append((request, output_schema))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeEvidenceResolver:
    def __init__(self, expected):
        self.expected = tuple(expected)

    def resolve(self, supplied):
        if tuple(supplied) != self.expected:
            raise EvidenceResolutionError("forged unit source")
        return self.expected


def contract_request(index: int = 1):
    built = build_contract_request(
        request_id=f"REQ_{index}",
        response_id=f"RESP_{index}",
        prompt_id=f"PROMPT_{index}",
        sources=sources(),
        allowed_requirement_ids=contract_inputs().allowed_requirement_ids,
        entry_points=contract_inputs().entry_points,
        supported_observables=contract_inputs().supported_observables,
        allowed_changes=contract_inputs().allowed_changes,
        limits=generation_limits(),
    )
    if index > 1:
        built = built.model_copy(
            update={"instruction": built.instruction + " Repair with exact verbatim quotes."}
        )
    return built


def test_contract_request_contains_only_grounded_sources_and_excludes_license():
    built = contract_request()
    assert built.stage is GenerationStage.INITIAL_AUTHORING
    assert tuple(context.context_id for context in built.contexts) == (
        "REQUEST",
        "BASELINE",
        "M3_RUNTIME_DISCOVERY",
    )
    assert all(context.source.kind != "source-response" for context in built.contexts)
    assert '"source_roots":["src"]' in built.instruction
    assert '"supported_observables":["combined terminal output","CLI exit code"]' in built.instruction


def test_contract_service_records_failed_candidate_then_freezes_grounded_repair(tmp_path):
    forged = contract_proposal(
        requirements=(
            contract_proposal().requirements[0].model_copy(
                update={
                    "evidence": (
                        contract_proposal().requirements[0].evidence[0].model_copy(
                            update={"quote": "forged"}
                        ),
                    )
                }
            ),
        )
    )
    provider = FakeProvider((provider_result(forged), provider_result(contract_proposal(), 2)))
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    service = ContractAuthoringService(
        provider=provider,
        store=store,
        resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    result = service.generate(
        (
            GenerationCandidate(request=contract_request()),
            GenerationCandidate(
                request=contract_request(2),
                diagnosis="The first proposal quoted text absent from its locator.",
                changed_input="Require verbatim quotes copied from admitted contexts.",
            ),
        ),
        contract_inputs(),
        sources(),
    )
    assert result.contract_ref == store.put_artifact(result.contract)
    assert len(result.journal_refs) == 2
    first = store.get_bytes(result.journal_refs[0]).decode()
    assert "GroundingError" in first and "forged" not in first
    assert len(result.contract.costs) == len(contract_inputs().costs) + 1
    assert result.generation.record.attempt_id == "attempt-2"


def test_contract_service_requires_diagnosed_changed_repairs_and_preserves_exhaustion(tmp_path):
    with pytest.raises(ValidationError):
        GenerationCandidate(request=contract_request(2), diagnosis="failure")
    provider = FakeProvider((provider_result({"malformed": True}),))
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    service = ContractAuthoringService(
        provider=provider,
        store=store,
        resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    with pytest.raises(AuthoringExhausted) as caught:
        service.generate((GenerationCandidate(request=contract_request()),), contract_inputs(), sources())
    assert len(caught.value.journal_refs) == 1
    assert "ValidationError" in store.get_bytes(caught.value.journal_refs[0]).decode()
    service.provider = FakeProvider((provider_result(contract_proposal(), 2),))
    repaired = service.generate(
        (
            GenerationCandidate(
                request=contract_request(2),
                diagnosis="The first output omitted every required proposal field.",
                changed_input="Add an explicit checklist of the six required proposal fields.",
            ),
        ),
        contract_inputs(),
        sources(),
        prior_journal_refs=caught.value.journal_refs,
    )
    assert len(repaired.journal_refs) == 2


def test_contract_service_rejects_identity_only_repair(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    first = contract_request()
    identity_only = first.model_copy(
        update={
            "request_id": "REQ_IDENTITY_ONLY",
            "response_id": "RESP_IDENTITY_ONLY",
            "prompt_id": "PROMPT_IDENTITY_ONLY",
        }
    )
    service = ContractAuthoringService(
        provider=FakeProvider(()), store=store,
        resolver=FakeEvidenceResolver(sources()), revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    with pytest.raises(ValueError, match="meaningful request input"):
        service.generate(
            (
                GenerationCandidate(request=first),
                GenerationCandidate(
                    request=identity_only,
                    diagnosis="retry",
                    changed_input="Only identities changed.",
                ),
            ),
            contract_inputs(), sources(),
        )


def test_successful_provider_publication_recovery_stops_and_resumes_without_model_call(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    successful = archived_provider_result(store, contract_request(), contract_proposal())
    pending_record = successful.record.model_copy(
        update={"success": False, "generation_succeeded": True, "publication_complete": False}
    )
    pending = GenerationProviderError("archive publication", pending_record)
    provider = FakeProvider((pending,))
    service = ContractAuthoringService(
        provider=provider, store=store, resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    )
    archived_before = set(store.root.glob("*.json"))
    with pytest.raises(GenerationProviderError) as caught:
        service.generate(
            (GenerationCandidate(request=contract_request()),), contract_inputs(), sources()
    )
    assert caught.value is pending
    assert set(store.root.glob("*.json")) == archived_before
    recovered_provider = FakeProvider(())
    service.provider = recovered_provider
    result = service.generate(
        (GenerationCandidate(request=contract_request()),), contract_inputs(), sources(),
        recovered_result=successful,
    )
    assert result.contract_ref == store.put_artifact(result.contract)
    assert recovered_provider.calls == []


@pytest.mark.parametrize("mutation", ("request", "cost"))
def test_recovered_contract_result_must_match_archived_operation(tmp_path, mutation):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    request = contract_request()
    recovered = archived_provider_result(store, request, contract_proposal())
    if mutation == "request":
        request = request.model_copy(update={"instruction": request.instruction + " changed"})
    else:
        recovered = recovered.model_copy(
            update={
                "cost": recovered.cost.model_copy(
                    update={"wall_seconds": 0.0, "cpu_seconds": 0.0}
                )
            }
        )
    provider = FakeProvider(())
    with pytest.raises(ValueError, match="recovered .* archive"):
        ContractAuthoringService(
            provider=provider, store=store, resolver=FakeEvidenceResolver(sources()),
            revision="2" * 40, evidence_scope="unit_diagnostic",
        ).generate(
            (GenerationCandidate(request=request),), contract_inputs(), sources(),
            recovered_result=recovered,
        )
    assert provider.calls == []


def test_failed_generation_publication_recovery_stops_before_repair(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    record = provider_result(contract_proposal()).record.model_copy(
        update={"success": False, "generation_succeeded": False, "publication_complete": False}
    )
    pending = GenerationProviderError("archive publication", record, recovery=object())
    provider = FakeProvider((pending, provider_result(contract_proposal(), 2)))
    with pytest.raises(GenerationProviderError) as caught:
        ContractAuthoringService(
            provider=provider, store=store, resolver=FakeEvidenceResolver(sources()),
            revision="2" * 40, evidence_scope="unit_diagnostic",
        ).generate(
            (
                GenerationCandidate(request=contract_request()),
                GenerationCandidate(
                    request=contract_request(2), diagnosis="retry", changed_input="repair"
                ),
            ),
            contract_inputs(), sources(),
        )
    assert caught.value is pending
    assert len(provider.calls) == 1


def test_recovered_failed_generation_journals_without_another_model_call(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    request = contract_request()
    recovered = archived_provider_error(store, request, contract_proposal())
    provider = FakeProvider(())
    service = ContractAuthoringService(
        provider=provider, store=store, resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    )
    original = store.put_bytes

    def fail_journal(data, kind, visibility):
        if kind == "contract-authoring-journal":
            raise OSError("disk")
        return original(data, kind, visibility)

    monkeypatch.setattr(store, "put_bytes", fail_journal)
    with pytest.raises(AuthoringJournalPublicationPending) as caught:
        service.generate(
            (GenerationCandidate(request=request),), contract_inputs(), sources(),
            recovered_error=recovered,
        )
    assert provider.calls == []
    monkeypatch.setattr(store, "put_bytes", original)
    refs = caught.value.replay(store)
    assert len(refs) == 1
    assert "malformed output" in store.get_bytes(refs[0]).decode()


def test_actual_registration_publication_replay_journals_without_execution(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)

    class NeverRun:
        calls = []
        def run(self, **kwargs):
            pytest.fail("unexpected runner execution")

    from feature_rl.generation import CodexConfig
    config = CodexConfig(executable='/must-not-run-codex')
    runner = NeverRun()
    failed = False

    def fail_attempt_once(data, kind, visibility):
        nonlocal failed
        if kind == "generation-attempt" and not failed:
            failed = True
            raise OSError("synthetic registration publication failure")
        return store.put_bytes(data, kind, visibility)

    service = ContractAuthoringService(
        provider=CodexGenerationProvider(
            config=config, archive=fail_attempt_once, runner=runner
        ),
        store=store, resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    )
    request = contract_request()
    with pytest.raises(GenerationProviderError) as caught:
        service.generate(
            (GenerationCandidate(request=request),), contract_inputs(), sources()
        )
    recovered = caught.value.replay_error(store.put_bytes)
    assert recovered.cost.note.startswith("Execution did not start")
    no_model = FakeProvider(())
    service.provider = no_model
    with pytest.raises(AuthoringExhausted) as rejected:
        service.generate(
            (GenerationCandidate(request=request),), contract_inputs(), sources(),
            recovered_error=recovered,
        )
    assert len(rejected.value.journal_refs) == 1
    assert runner.calls == []
    assert no_model.calls == []


def test_recovered_response_receipt_cap_includes_base64_expansion(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    request = contract_request()
    response = {"stdout_base64": "A" * 1_262_000, "stderr_base64": ""}
    recovered = archived_provider_result(
        store, request, contract_proposal(), response_payload=response
    )
    provider = FakeProvider(())
    result = ContractAuthoringService(
        provider=provider, store=store, resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    ).generate(
        (GenerationCandidate(request=request),), contract_inputs(), sources(),
        recovered_result=recovered,
    )
    assert result.contract_ref.kind == "RequirementContract"
    assert provider.calls == []


def test_rejection_journal_publication_failure_is_replayable(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    service = ContractAuthoringService(
        provider=FakeProvider((provider_result({"malformed": True}),)),
        store=store, resolver=FakeEvidenceResolver(sources()), revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    original = store.put_bytes
    def fail_journal(data, kind, visibility):
        if kind == "contract-authoring-journal":
            raise OSError("disk")
        return original(data, kind, visibility)
    monkeypatch.setattr(store, "put_bytes", fail_journal)
    with pytest.raises(AuthoringJournalPublicationPending) as caught:
        service.generate(
            (GenerationCandidate(request=contract_request()),), contract_inputs(), sources()
        )
    monkeypatch.setattr(store, "put_bytes", original)
    refs = caught.value.replay(store)
    assert len(refs) == 1
    assert "ValidationError" in store.get_bytes(refs[0]).decode()


def test_contract_publication_failure_replays_without_another_generation(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    provider = FakeProvider((provider_result(contract_proposal()),))
    service = ContractAuthoringService(
        provider=provider,
        store=store,
        resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    original = store.put_artifact
    monkeypatch.setattr(store, "put_artifact", lambda artifact: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(AuthoringPublicationPending) as caught:
        service.generate(
            (GenerationCandidate(request=contract_request()),), contract_inputs(), sources()
        )
    assert len(provider.calls) == 1
    monkeypatch.setattr(store, "put_artifact", original)
    artifact_ref, journal_refs = caught.value.replay(store)
    assert store.get_artifact(artifact_ref) == caught.value.artifact
    assert len(journal_refs) == 1
    assert len(provider.calls) == 1
















class FakeRuntime:
    def __init__(self, store, stdout, *, reason="completed", exit_code=0):
        self.store = store
        self.stdout = stdout
        self.reason = reason
        self.exit_code = exit_code
        self.calls = []
        from m4_fixtures import runtime_policy
        self.profile = runtime_policy().profile

    def open_workspace(self, prepared, *, role):
        self.calls.append(("open_workspace", prepared, role))
        return "handle"

    def recipe(self, prepared):
        self.calls.append(("recipe", prepared))
        return SimpleNamespace(baseline=BASELINE)

    def build_snapshot(self, handle):
        self.calls.append(("build_snapshot", handle))
        return SimpleNamespace(evidence=ref("build-receipt", "7", Visibility.PRIVATE), cost=cost("construction"))

    def execute(self, handle, request, *, build):
        self.calls.append(("execute", handle, request, build))
        return SimpleNamespace(
            reason=self.reason,
            failure_category="none" if self.reason == "completed" else "candidate",
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=b"",
            cleanup_verified=True,
            oom_killed=False,
            evidence=ref("execution-receipt", "8", Visibility.PRIVATE),
            cost=cost("execution"),
        )

    def close(self, handle):
        self.calls.append(("close", handle))

    def publish(self, value, kind, visibility):
        return self.store.put_bytes(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
            kind,
            visibility,
        )


def discovery_stdout(**updates) -> bytes:
    value = {
        "project_name": "click", "project_version": "8.3.3", "interpreter_version": "3.12.14",
        "module_files": {"click": "/workspace/site/click/__init__.py"},
        "entry_points": ["click.Group", "click.group", "click.command", "click.testing.CliRunner"],
        "supported_observables": ["CLI exit code", "combined terminal output"],
        "signatures": {name: None for name in ("click.Group", "click.group", "click.command", "click.testing.CliRunner")},
    }
    value.update(updates)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_click_discovery_runs_installed_baseline_and_publishes_sanitized_author_context(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    runtime = FakeRuntime(store, discovery_stdout())
    result = RuntimeDiscoveryService(runtime=runtime).discover(PREPARED)
    assert result.observation.project_version == "8.3.3"
    assert result.context.source.visibility is Visibility.AUTHORING
    assert result.context.role == "baseline"
    assert result.private_evidence == (
        ref("build-receipt", "7", Visibility.PRIVATE),
        ref("execution-receipt", "8", Visibility.PRIVATE),
    )
    execute = [call for call in runtime.calls if call[0] == "execute"][0]
    assert execute[2].save_source is False
    assert execute[2].command.argv[:2] == ("/usr/local/bin/python", "-c")
    assert runtime.calls[-1] == ("close", "handle")


@pytest.mark.parametrize(
    "stdout, reason, exit_code",
    [
        (discovery_stdout(project_version="8.4.0"), "completed", 0),
        (discovery_stdout(module_files={"click": "/workspace/source/src/click/__init__.py"}), "completed", 0),
        (b'{"click_version":"8.3.3","click_version":"forged"}', "completed", 0),
        (discovery_stdout(), "command_failed", 1),
    ],
)
def test_click_discovery_rejects_wrong_identity_malformed_output_and_failed_execution(
    tmp_path, stdout, reason, exit_code
):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    runtime = FakeRuntime(store, stdout, reason=reason, exit_code=exit_code)
    with pytest.raises(ValueError):
        RuntimeDiscoveryService(runtime=runtime).discover(PREPARED)
    assert runtime.calls[-1] == ("close", "handle")


def source_archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def source_archive_with_directory_and_link(*, link: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo("src/click")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        info = tarfile.TarInfo("src/click/core.py")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"one\n"))
        if link:
            linked = tarfile.TarInfo("src/click/linked.py")
            linked.type = tarfile.SYMTYPE
            linked.linkname = "core.py"
            archive.addfile(linked)
    return output.getvalue()


def test_baseline_retriever_accepts_directory_metadata_but_rejects_links():
    policy = RetrievalPolicy(
        allowed_paths=("src/click/core.py",),
        max_archive_bytes=1024 * 1024,
        max_files=10,
        max_selected_bytes=64,
        max_spans=2,
    )
    request = (RetrievalRequest(context_id="CORE", path="src/click/core.py", line_ranges=((1, 1),)),)
    result = BaselineRetriever(
        baseline=BASELINE, archive=source_archive_with_directory_and_link(), policy=policy
    ).retrieve(request)
    assert result.sources[0].text == "one\n"
    with pytest.raises(RetrievalRejected, match="unsupported members"):
        BaselineRetriever(
            baseline=BASELINE,
            archive=source_archive_with_directory_and_link(link=True),
            policy=policy,
        ).retrieve(request)


def test_baseline_retriever_enforces_allowlist_ranges_and_records_exact_spans():
    archive = source_archive(
        {
            "src/click/core.py": b"one\ntwo\nthree\nfour\n",
            "tests/test_core.py": b"test one\ntest two\n",
        }
    )
    retriever = BaselineRetriever(
        baseline=BASELINE,
        archive=archive,
        policy=RetrievalPolicy(
            allowed_paths=("src/click/core.py",),
            max_archive_bytes=1024 * 1024,
            max_files=10,
            max_selected_bytes=64,
            max_spans=2,
        ),
    )
    result = retriever.retrieve(
        (
            RetrievalRequest(
                context_id="CORE",
                path="src/click/core.py",
                line_ranges=((2, 3),),
            ),
        )
    )
    assert result.sources[0].text == "two\nthree\n"
    assert result.sources[0].locator == "src/click/core.py:2-3"
    assert result.receipt.selected_bytes == 10
    assert result.receipt.requests[0].text_sha256


@pytest.mark.parametrize(
    "path, ranges",
    [
        ("tests/test_core.py", ((1, 1),)),
        ("../secret", ((1, 1),)),
        ("src/click/core.py", ((0, 1),)),
        ("src/click/core.py", ((1, 99),)),
    ],
)
def test_baseline_retriever_rejects_unallowlisted_unsafe_or_invalid_expansions(path, ranges):
    retriever = BaselineRetriever(
        baseline=BASELINE,
        archive=source_archive({"src/click/core.py": b"one\ntwo\n"}),
        policy=RetrievalPolicy(
            allowed_paths=("src/click/core.py",),
            max_archive_bytes=1024 * 1024,
            max_files=10,
            max_selected_bytes=64,
            max_spans=2,
        ),
    )
    with pytest.raises((ValidationError, RetrievalRejected)):
        retriever.retrieve(
            (RetrievalRequest(context_id="SPAN", path=path, line_ranges=ranges),)
        )


def test_authoring_evidence_resolver_reconstructs_exact_bytes_and_rejects_forged_text(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    request_ref = store.put_bytes(
        b"Unknown commands should offer a close command suggestion.",
        "authoring-request",
        Visibility.AUTHORING,
    )
    archive_ref = store.put_bytes(
        source_archive({"src/click/core.py": b"one\ntwo\nthree\n"}),
        "source-archive",
        Visibility.AUTHORING,
    )
    discovery_text = bound_discovery_text(baseline=archive_ref)
    discovery_ref = store.put_bytes(
        discovery_text.encode(), "runtime-discovery", Visibility.AUTHORING
    )
    resolver = AuthoringEvidenceResolver(
        store=store,
        request=request_ref,
        baseline=archive_ref,
        runtime_discovery=discovery_ref,
        public_checks=(),
        retrieval_policy=RetrievalPolicy(
            allowed_paths=("src/click/core.py",),
            max_archive_bytes=1024 * 1024,
            max_files=10,
            max_selected_bytes=64,
            max_spans=2,
        ),
    )
    admitted = (
        GroundedSource(
            context_id="REQUEST",
            role="request",
            source=request_ref,
            locator="authoring-request:whole",
            text="Unknown commands should offer a close command suggestion.",
            provenance_label="reconstructed_specification",
        ),
        GroundedSource(
            context_id="CORE",
            role="baseline",
            source=archive_ref,
            locator="src/click/core.py:2-3",
            text="two\nthree\n",
            provenance_label="existing_obligation",
        ),
        GroundedSource(
            context_id="M3_RUNTIME_DISCOVERY",
            role="baseline",
            source=discovery_ref,
            locator="m3:runtime-discovery-v1",
            text=discovery_text,
            provenance_label="existing_obligation",
        ),
    )
    assert resolver.resolve(admitted) == admitted
    forged = admitted[1].model_copy(update={"text": "forged\n"})
    with pytest.raises(EvidenceResolutionError, match="does not match"):
        resolver.resolve((admitted[0], forged, admitted[2]))


def test_contract_service_rejects_request_contexts_that_differ_from_resolved_sources(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    provider = FakeProvider((provider_result(contract_proposal()),))
    service = ContractAuthoringService(
        provider=provider,
        store=store,
        resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40,
        evidence_scope="unit_diagnostic",
    )
    built = contract_request()
    forged = built.model_copy(
        update={
            "contexts": (
                built.contexts[0].model_copy(update={"text": "forged source text"}),
                *built.contexts[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="contexts"):
        service.generate(
            (GenerationCandidate(request=forged),), contract_inputs(), sources()
        )
    assert provider.calls == []
