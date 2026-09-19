from __future__ import annotations

from datetime import datetime, timezone
import json
import io
import tarfile
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from feature_rl.artifacts import ArtifactStore
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
    ContractAuthoringService,
    ContractFinalizationInputs,
    ContractFinalizer,
    ClickDiscoveryError,
    ClickDiscoveryService,
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
from feature_rl.scenarios import (
    ScenarioAuthoringService,
    ScenarioFinalizationInputs,
    ScenarioFinalizer,
    ScenarioJoinError,
    ScenarioPlanProposal,
    build_scenario_request,
)
from feature_rl.generation import (
    GenerationCallRecord,
    GenerationLimits,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)


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
DISCOVERY = ref("click-runtime-discovery", "9")
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
            context_id="M3_CLICK_DISCOVERY",
            role="baseline",
            source=DISCOVERY,
            locator="m3:click-runtime-discovery-v1",
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
        public_checks=(PUBLIC_CHECK,),
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


def scenario_proposal(**updates) -> ScenarioPlanProposal:
    values = {
        "scenarios": (
            {
                "scenario_id": "SCENARIO_SUGGESTION",
                "requirement_ids": ("FEATURE_COMMAND_SUGGESTION",),
                "preconditions": ("A group exposes a command with a close name.",),
                "actions": ("Invoke the group with an unknown close command name.",),
                "observations": ("combined terminal output",),
                "expected_relation": "The output contains a close available command name.",
                "input_domain": "Unknown names with exactly one admitted close match.",
                "oracle_origin": {
                    "source": REQUEST,
                    "locator": "request:1-4",
                    "quote": "close command suggestion",
                    "provenance_label": "reconstructed_specification",
                },
                "reset_needs": (),
            },
            {
                "scenario_id": "SCENARIO_COMPAT",
                "requirement_ids": ("COMPAT_GROUP_DISPATCH",),
                "preconditions": ("A group exposes an existing command.",),
                "actions": ("Invoke the existing command.",),
                "observations": ("CLI exit code",),
                "expected_relation": "The existing command invocation remains successful.",
                "input_domain": "An exact existing command name.",
                "oracle_origin": {
                    "source": BASELINE,
                    "locator": "src/click/core.py:1-20",
                    "quote": "public command dispatch entry point",
                    "provenance_label": "existing_obligation",
                },
                "reset_needs": (),
            },
        )
    }
    return ScenarioPlanProposal.model_validate(values | updates)


def scenario_inputs(contract_ref: ArtifactRef) -> ScenarioFinalizationInputs:
    return ScenarioFinalizationInputs(
        contract=contract_ref,
        supported_observables=("combined terminal output", "CLI exit code"),
        seed_policy=SeedPolicy(
            algorithm="sha256-case-family-v1", seeds=(0, 1, 2), same_cases_within_group=True
        ),
        visibility=Visibility.PRIVATE,
        provenance=provenance(REQUEST, BASELINE, contract_ref),
        costs=(cost(),),
    )


def test_scenario_proposal_preserves_m0_constraints_without_controller_fields():
    assert set(ScenarioPlanProposal.model_fields) == {"scenarios"}
    with pytest.raises(ValidationError):
        scenario_proposal(scenarios=())


def test_scenario_finalizer_enforces_frozen_contract_ids_seed_and_grounding():
    contract = finalized_contract()
    contract_ref = ref("RequirementContract", "f")
    plan = ScenarioFinalizer().finalize(
        scenario_proposal(), contract, scenario_inputs(contract_ref), sources(),
        expected_contract=contract_ref,
    )
    assert plan.contract == contract_ref
    assert plan.mandatory_requirement_ids == (
        "FEATURE_COMMAND_SUGGESTION",
        "COMPAT_GROUP_DISPATCH",
    )
    assert plan.seed_policy.same_cases_within_group is True


def test_scenario_finalizer_rejects_unknown_and_incomplete_requirement_coverage():
    contract = finalized_contract()
    contract_ref = ref("RequirementContract", "f")
    proposals = (
        scenario_proposal(
            scenarios=(
                scenario_proposal().scenarios[0].model_copy(
                    update={"requirement_ids": ("UNKNOWN",)}
                ),
                scenario_proposal().scenarios[1],
            )
        ),
        scenario_proposal(scenarios=(scenario_proposal().scenarios[0],)),
    )
    for proposal in proposals:
        with pytest.raises(ScenarioJoinError):
            ScenarioFinalizer().finalize(
                proposal, contract, scenario_inputs(contract_ref), sources(),
                expected_contract=contract_ref,
            )


def test_scenario_finalizer_rejects_nonidentical_contract_and_variable_case_policy():
    contract = finalized_contract()
    contract_ref = ref("RequirementContract", "f")
    wrong_ref = ref("RequirementContract", "e")
    with pytest.raises(ScenarioJoinError, match="contract"):
        ScenarioFinalizer().finalize(
            scenario_proposal(), contract, scenario_inputs(wrong_ref), sources(),
            expected_contract=contract_ref,
        )
    inputs = scenario_inputs(contract_ref).model_copy(
        update={
            "seed_policy": SeedPolicy(
                algorithm="sha256-case-family-v1",
                seeds=(0,),
                same_cases_within_group=False,
            )
        }
    )
    with pytest.raises(ScenarioJoinError, match="same cases"):
        ScenarioFinalizer().finalize(
            scenario_proposal(), contract, inputs, sources(),
            expected_contract=contract_ref,
        )


def generation_limits() -> GenerationLimits:
    return GenerationLimits(
        measurement_profile="larger_unqualified",
        wall_seconds=120,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        file_size_bytes=1_048_576,
        input_tokens=8_192,
        output_tokens=4_096,
        mlx_memory_guideline_bytes=3_758_096_384,
        mlx_wired_limit_bytes=3_758_096_384,
        mlx_cache_limit_bytes=0,
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
            input_token_ids=(1, 2),
            output_tokens=1,
            token_ids=(3,),
            selected_model_logprobs=(-0.1,),
            sampling_policy="greedy_argmax",
            behavior_logprobs=None,
            finish_reason="stop",
            truncated=False,
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
    return build_contract_request(
        request_id=f"REQ_{index}",
        response_id=f"RESP_{index}",
        prompt_id=f"PROMPT_{index}",
        sources=sources(),
        allowed_requirement_ids=contract_inputs().allowed_requirement_ids,
        entry_points=contract_inputs().entry_points,
        supported_observables=contract_inputs().supported_observables,
        allowed_changes=contract_inputs().allowed_changes,
        limits=generation_limits(),
        seed=0,
    )


def test_contract_request_contains_only_grounded_sources_and_excludes_license():
    built = contract_request()
    assert built.stage is GenerationStage.INITIAL_AUTHORING
    assert tuple(context.context_id for context in built.contexts) == (
        "REQUEST",
        "BASELINE",
        "M3_CLICK_DISCOVERY",
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


def test_scenario_request_and_service_resolve_exact_stored_contract(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    contract = finalized_contract()
    contract_ref = store.put_artifact(contract)
    built = build_scenario_request(
        request_id="SCENARIO_REQ_1",
        response_id="SCENARIO_RESP_1",
        prompt_id="SCENARIO_PROMPT_1",
        contract=contract,
        contract_ref=contract_ref,
        sources=sources(),
        limits=generation_limits(),
        seed=0,
    )
    assert built.stage is GenerationStage.SCENARIO_PLANNING
    assert tuple(context.role for context in built.contexts)[-1] == "contract"
    provider = FakeProvider((provider_result(scenario_proposal()),))
    result = ScenarioAuthoringService(
        provider=provider,
        store=store,
        resolver=FakeEvidenceResolver(sources()),
        revision="2" * 40,
        evidence_scope="unit_diagnostic",
    ).generate(
        (GenerationCandidate(request=built),),
        scenario_inputs(contract_ref),
        sources(),
    )
    assert result.plan.contract == contract_ref
    assert store.get_artifact(result.plan_ref) == result.plan


class FakeRuntime:
    def __init__(self, store, stdout, *, reason="completed", exit_code=0):
        self.store = store
        self.stdout = stdout
        self.reason = reason
        self.exit_code = exit_code
        self.calls = []

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
        "click_version": "8.3.3",
        "module_file": "/workspace/site/click/__init__.py",
        "entry_points": ["click.Group", "click.group", "click.command", "click.testing.CliRunner"],
        "supported_observables": ["CLI exit code", "combined terminal output"],
        "resolve_command_signature": "(self, ctx, args)",
        "exact_command": {"exit_code": 0, "output": "ready\n", "exception_type": None},
        "unknown_command": {
            "exit_code": 2,
            "output": "Usage: cli [OPTIONS] COMMAND [ARGS]...\nTry 'cli --help' for help.\n\nError: No such command 'statuz'.\n",
            "exception_type": "SystemExit",
        },
        "no_such_command_exported": False,
    }
    value.update(updates)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_click_discovery_runs_installed_baseline_and_publishes_sanitized_author_context(tmp_path):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    runtime = FakeRuntime(store, discovery_stdout())
    result = ClickDiscoveryService(runtime=runtime).discover(PREPARED)
    assert result.observation.click_version == "8.3.3"
    assert result.context.source.visibility is Visibility.AUTHORING
    assert result.context.role == "baseline"
    assert result.private_evidence == (
        ref("build-receipt", "7", Visibility.PRIVATE),
        ref("execution-receipt", "8", Visibility.PRIVATE),
    )
    execute = [call for call in runtime.calls if call[0] == "execute"][0]
    assert execute[2].save_source is False
    assert execute[2].command.argv[:2] == ("python", "-c")
    assert runtime.calls[-1] == ("close", "handle")


@pytest.mark.parametrize(
    "stdout, reason, exit_code",
    [
        (discovery_stdout(click_version="8.4.0"), "completed", 0),
        (discovery_stdout(module_file="/workspace/source/src/click/__init__.py"), "completed", 0),
        (b'{"click_version":"8.3.3","click_version":"forged"}', "completed", 0),
        (discovery_stdout(), "command_failed", 1),
    ],
)
def test_click_discovery_rejects_wrong_identity_malformed_output_and_failed_execution(
    tmp_path, stdout, reason, exit_code
):
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    runtime = FakeRuntime(store, stdout, reason=reason, exit_code=exit_code)
    with pytest.raises(ClickDiscoveryError):
        ClickDiscoveryService(runtime=runtime).discover(PREPARED)
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
        discovery_text.encode(), "click-runtime-discovery", Visibility.AUTHORING
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
            context_id="M3_CLICK_DISCOVERY",
            role="baseline",
            source=discovery_ref,
            locator="m3:click-runtime-discovery-v1",
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
