"""Reproduce the bounded B-only Click contract and scenario authoring run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from transformers import AutoTokenizer

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import (
    ActorRole,
    AllowedChanges,
    ArtifactRef,
    CostRecord,
    DependencyPin,
    EvidenceRecord,
    Provenance,
    SeedPolicy,
    Visibility,
)
from feature_rl.environments import DockerEngine, EnvironmentRuntime, SandboxPolicy
from feature_rl.generation import BackendConfig, GenerationLimits, LocalGenerationProvider
from feature_rl.generation.backend import MODEL_MANIFEST_SHA256, MODEL_REVISION
from feature_rl.generation.provider import _prompt
from feature_rl.requirements import (
    AuthoringEvidenceResolver,
    BaselineRetriever,
    ClickDiscoveryService,
    ClickDiscoveryObservation,
    ContractAuthoringService,
    ContractFinalizationInputs,
    GenerationCandidate,
    GroundedSource,
    RequirementContractProposal,
    RetrievalPolicy,
    RetrievalRequest,
    build_contract_request,
)
from feature_rl.scenarios import (
    ScenarioAuthoringService,
    ScenarioFinalizationInputs,
    ScenarioPlanProposal,
    build_scenario_request,
)


ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = ROOT / "docs/evidence/M2/authoring-production"
INPUT = ROOT / "docs/evidence/M2/coordinator-authoring-input.json"
SOURCE_STORE = ROOT / ".feature-rl/research/M1/production-store"
WORK = ROOT / ".feature-rl/research/M2/authoring-production"
STORE_PATH = WORK / "store"
STATE_PATH = EVIDENCE / "state.json"
MODEL_DIRECTORY = (
    ROOT
    / ".feature-rl/research/M2/model/mlx-community--Qwen3-4B-Instruct-2507-4bit"
    / MODEL_REVISION
)
MODEL_MANIFEST = ROOT / ".feature-rl/research/M2/model-acquisition.json"
DEPENDENCY_MANIFEST = ROOT / ".feature-rl/research/M2/dependency-lock.json"
REVISION = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def ref(value: object) -> ArtifactRef:
    return ArtifactRef.model_validate_json(canonical_json(value))


def fixed_cost(category: str, note: str) -> CostRecord:
    return CostRecord(
        category=category,
        wall_seconds=None,
        cpu_seconds=None,
        gpu_seconds=None,
        input_tokens=None,
        output_tokens=None,
        human_minutes=None,
        usd=None,
        measurement="unknown",
        note=note,
    )


def load_inputs(store: ArtifactStore) -> tuple[dict, dict[str, ArtifactRef], dict[str, bytes]]:
    raw = INPUT.read_bytes()
    if digest(raw) != "6172bbb28f9cd9275db21bc47ea55af922fe3d295758f7c443881736d56aebc0":
        raise RuntimeError("sanitized coordinator input identity changed")
    setup = json.loads(raw)
    if Path(setup["source_store"]) != Path(".feature-rl/research/M1/production-store"):
        raise RuntimeError("unexpected authoring source store")
    source = ArtifactStore(SOURCE_STORE, ActorRole.AUTHOR)
    refs = {name: ref(value) for name, value in setup["authoring_view"].items()}
    payloads = {
        name: source.get_bytes(value, max_envelope_bytes=100_000_000, max_payload_bytes=80_000_000)
        for name, value in refs.items()
    }
    for name, artifact in refs.items():
        copied = store.put_bytes(payloads[name], artifact.kind, artifact.visibility)
        if copied != artifact:
            raise RuntimeError(f"copied authoring artifact differs: {name}")
    return setup, refs, payloads


def source_evidence(refs: dict[str, ArtifactRef]) -> EvidenceRecord:
    return EvidenceRecord(
        producer="M2 bounded authoring controller",
        command=("python", "docs/evidence/M2/authoring-production/run.py", "contract"),
        recorded_at=datetime.now(timezone.utc),
        exit_status=0,
        artifacts=(refs["baseline"], refs["request_evidence"], refs["license_text"]),
        revision=REVISION,
        scope="real_integration",
    )


def prepare_runtime(
    store: ArtifactStore, setup: dict, refs: dict[str, ArtifactRef]
) -> tuple[EnvironmentRuntime, object]:
    pins = []
    expected = {item["name"]: item for item in setup["dependency_wheels"]}
    for name, item in expected.items():
        path = ROOT / item["local_path"]
        data = path.read_bytes()
        if digest(data) != item["sha256"]:
            raise RuntimeError(f"wheel identity changed: {name}")
        wheel_ref = store.put_bytes(data, "dependency-wheel", Visibility.AUTHORING)
        pins.append(
            DependencyPin(
                name=name,
                version=item["version"],
                artifact=wheel_ref,
                sha256=item["sha256"],
            )
        )
    engine = DockerEngine(
        state_root=WORK / "docker-state",
        socket_path=Path(setup["socket_path"]),
        policy=SandboxPolicy(),
    )
    engine.qualify_boundary()
    runtime = EnvironmentRuntime(store=store, engine=engine, revision=REVISION)
    prepared = runtime.create_click_recipe(
        refs["baseline"], tuple(pins), source_evidence=source_evidence(refs)
    )
    return runtime, prepared


def _stored_refs(kind: str) -> tuple[ArtifactRef, ...]:
    found = []
    for path in STORE_PATH.glob("*.json"):
        envelope = json.loads(path.read_text())
        if envelope.get("kind") == kind:
            found.append(
                ArtifactRef(
                    sha256=path.stem,
                    kind=kind,
                    schema_version=envelope["schema_version"],
                    visibility=Visibility(envelope["visibility"]),
                    encoding=envelope["encoding"],
                )
            )
    return tuple(found)


def prior_contract_journals(store: ArtifactStore) -> tuple[ArtifactRef, ...]:
    ordered = []
    for artifact in _stored_refs("contract-authoring-journal"):
        payload = json.loads(
            store.get_bytes(
                artifact, max_envelope_bytes=96 * 1024, max_payload_bytes=64 * 1024
            )
        )
        if payload.get("status") != "rejected":
            continue
        ordered.append((payload["attempt_index"], artifact, payload))
    ordered.sort(key=lambda item: item[0])
    if [item[0] for item in ordered] != list(range(1, len(ordered) + 1)):
        raise RuntimeError("retained contract journals are not sequential")
    return tuple(item[1] for item in ordered)


def _receipt_cost(store: ArtifactStore, evidence: ArtifactRef, category: str) -> CostRecord:
    value = json.loads(
        store.get_bytes(evidence, max_envelope_bytes=3 * 1024 * 1024, max_payload_bytes=2 * 1024 * 1024)
    )
    return CostRecord(
        category=category,
        wall_seconds=value["lifecycle_wall_seconds"],
        cpu_seconds=value["maximum_cpu_seconds"],
        gpu_seconds=None,
        input_tokens=None,
        output_tokens=None,
        human_minutes=None,
        usd=None,
        measurement="partial",
        note=(
            "Recovered from the exact retained M3 receipt after the authoring controller "
            "failed during later inert retrieval; no discovery rerun."
        ),
    )


def retained_discovery(store: ArtifactStore):
    matches = _stored_refs("click-runtime-discovery")
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("expected exactly one retained Click discovery")
    source = matches[0]
    text = store.get_bytes(source, max_envelope_bytes=256 * 1024, max_payload_bytes=128 * 1024).decode()
    observation = ClickDiscoveryObservation.model_validate_json(text)
    private = (
        ArtifactRef(
            sha256=observation.build_evidence_sha256, kind="environment-execution",
            schema_version=1, visibility=Visibility.PRIVATE, encoding="bytes",
        ),
        ArtifactRef(
            sha256=observation.execution_evidence_sha256, kind="environment-execution",
            schema_version=1, visibility=Visibility.PRIVATE, encoding="bytes",
        ),
    )
    context = GroundedSource(
        context_id="M3_CLICK_DISCOVERY",
        role="baseline",
        source=source,
        locator="m3:click-runtime-discovery-v1",
        text=text,
        provenance_label="existing_obligation",
    )
    return SimpleNamespace(
        observation=observation,
        context=context,
        private_evidence=private,
        costs=(
            _receipt_cost(store, private[0], "construction"),
            _receipt_cost(store, private[1], "execution"),
        ),
    )


def retrieval_policy() -> RetrievalPolicy:
    return RetrievalPolicy(
        allowed_paths=(
            "src/click/core.py",
            "src/click/exceptions.py",
            "tests/test_options.py",
            "tests/test_commands.py",
            "docs/commands-and-groups.md",
        ),
        max_archive_bytes=16 * 1024 * 1024,
        max_files=2000,
        max_selected_bytes=48 * 1024,
        max_spans=8,
        max_expanded_bytes=16 * 1024 * 1024,
    )


def retrieve(refs: dict[str, ArtifactRef], baseline: bytes):
    requests = (
        RetrievalRequest(context_id="B_CORE_GROUP", path="src/click/core.py", line_ranges=((1532, 1560), (1878, 1965))),
        RetrievalRequest(context_id="B_OPTION_ERROR", path="src/click/exceptions.py", line_ranges=((212, 243),)),
        RetrievalRequest(context_id="B_OPTION_TEST", path="tests/test_options.py", line_ranges=((136, 160),)),
        RetrievalRequest(context_id="B_COMMAND_REGRESSION", path="tests/test_commands.py", line_ranges=((8, 41),)),
        RetrievalRequest(context_id="B_GROUP_DOC", path="docs/commands-and-groups.md", line_ranges=((72, 100),)),
    )
    return BaselineRetriever(
        baseline=refs["baseline"], archive=baseline, policy=retrieval_policy()
    ).retrieve(requests)


def limits(input_tokens: int) -> GenerationLimits:
    return GenerationLimits(
        measurement_profile="larger_unqualified",
        wall_seconds=120.0,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        file_size_bytes=1_048_576,
        input_tokens=input_tokens,
        output_tokens=4096,
        mlx_memory_guideline_bytes=3_758_096_384,
        mlx_wired_limit_bytes=3_758_096_384,
        mlx_cache_limit_bytes=0,
        physical_footprint_kill_bytes=4_294_967_296,
        physical_footprint_poll_seconds=0.02,
        declared_memory_ceiling_bytes=5_368_709_120,
    )


def tokenizer_and_backend() -> tuple[object, BackendConfig]:
    if digest(MODEL_MANIFEST.read_bytes()) != MODEL_MANIFEST_SHA256:
        raise RuntimeError("model acquisition manifest changed")
    backend = BackendConfig(
        python_executable=Path(sys.executable),
        model_directory=MODEL_DIRECTORY,
        model_manifest=MODEL_MANIFEST,
        dependency_manifest=DEPENDENCY_MANIFEST,
    )
    backend.verify()
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_DIRECTORY, local_files_only=True, trust_remote_code=False
    )
    return tokenizer, backend


def measure(tokenizer, request, schema, label: str) -> tuple[object, dict]:
    provisional = request.model_copy(update={"limits": limits(16_384)})
    prompt = _prompt(provisional, schema)
    plain_ids = tokenizer.encode(prompt, add_special_tokens=False)
    templated = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}],
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
    )["input_ids"]
    cap = math.ceil((len(templated) * 1.2) / 1024) * 1024
    if cap > 16_384:
        raise RuntimeError(f"{label} measured input does not fit the authorized cap")
    frozen = request.model_copy(update={"limits": limits(cap)})
    frozen = type(request).model_validate(frozen)
    final_prompt = _prompt(frozen, schema)
    if final_prompt != prompt:
        # The serialized limit changes the prompt; measure the final envelope once.
        plain_ids = tokenizer.encode(final_prompt, add_special_tokens=False)
        templated = tokenizer.apply_chat_template(
            [{"role": "user", "content": final_prompt}], tokenize=True,
            add_generation_prompt=True, enable_thinking=False,
        )["input_ids"]
        cap = math.ceil((len(templated) * 1.2) / 1024) * 1024
        frozen = type(request).model_validate(request.model_copy(update={"limits": limits(cap)}))
        final_prompt = _prompt(frozen, schema)
    request_bytes = canonical_json(frozen.model_dump(mode="json"))
    schema_bytes = canonical_json(schema.model_json_schema())
    record = {
        "label": label,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "revision": REVISION,
        "request_bytes": len(request_bytes),
        "request_sha256": digest(request_bytes),
        "schema_bytes": len(schema_bytes),
        "schema_sha256": digest(schema_bytes),
        "prompt_bytes": len(final_prompt.encode()),
        "prompt_sha256": digest(final_prompt.encode()),
        "plain_prompt_tokens": len(plain_ids),
        "templated_input_tokens": len(templated),
        "token_ids_sha256": digest(canonical_json(templated)),
        "declared_input_tokens": cap,
        "declared_output_tokens": 4096,
        "contexts": [item.model_dump(mode="json") for item in frozen.contexts],
        "native_calls_before_measurement": 0,
    }
    write_json(EVIDENCE / f"{label}-measurement.json", record)
    return frozen, record


def resolver(store, refs, discovery_ref):
    return AuthoringEvidenceResolver(
        store=store,
        request=refs["request_evidence"],
        baseline=refs["baseline"],
        runtime_discovery=discovery_ref,
        public_checks=(),
        retrieval_policy=retrieval_policy(),
    )


def contract() -> None:
    if STATE_PATH.exists():
        raise RuntimeError("production state already exists; refusing to repeat discovery/generation")
    store = ArtifactStore(STORE_PATH, ActorRole.CONTROLLER)
    setup, refs, payloads = load_inputs(store)
    discovered = retained_discovery(store)
    if discovered is None:
        runtime, prepared = prepare_runtime(store, setup, refs)
        discovered = ClickDiscoveryService(runtime=runtime).discover(prepared)
        recipe_ref = prepared.recipe
    else:
        recipe_ref = discovered.observation.recipe
    retrieved = retrieve(refs, payloads["baseline"])
    retrieval_ref = store.put_bytes(
        canonical_json(retrieved.receipt.model_dump(mode="json")),
        "authoring-retrieval-receipt",
        Visibility.AUTHORING,
    )
    request_source = GroundedSource(
        context_id="REQUEST",
        role="request",
        source=refs["request_evidence"],
        locator="authoring-request:whole",
        text=payloads["request_evidence"].decode(),
        provenance_label="reconstructed_specification",
    )
    sources = (request_source, *retrieved.sources, discovered.context)
    tokenizer, backend = tokenizer_and_backend()
    allowed = AllowedChanges(
        source_roots=("src/click",), forbidden_paths=("tests",), dependencies="forbidden",
        dependency_artifacts=(), additional_artifact_types=(),
    )
    prior_journals = prior_contract_journals(store)
    if len(prior_journals) > 2:
        raise RuntimeError("contract repair budget is exhausted")
    namespace_size = 4 if len(prior_journals) >= 2 else 8
    draft = build_contract_request(
        request_id="CLICK_CONTRACT_1", response_id="CLICK_CONTRACT_RESPONSE_1",
        prompt_id="CLICK_CONTRACT_PROMPT_1", sources=sources,
        allowed_requirement_ids=tuple(
            f"REQ_{index}" for index in range(1, namespace_size + 1)
        ),
        entry_points=discovered.observation.entry_points,
        supported_observables=discovered.observation.supported_observables,
        allowed_changes=allowed, limits=limits(16_384), seed=0,
    )
    diagnosis = None
    changed_input = None
    label = "contract"
    if prior_journals:
        repair = len(prior_journals)
        label = f"contract-repair-{repair}"
        if repair == 1:
            diagnosis = (
                "The previous worker reached the fixed 120-second deadline after emitting "
                "a long unfinished response."
            )
            changed_input = (
                "Require concise one-sentence fields, combine evidence where possible, and "
                "exclude commentary while preserving every supported semantic obligation."
            )
        else:
            diagnosis = (
                "The first concision repair still reached the fixed deadline after expanding "
                "all eight namespace IDs into long and duplicative obligations."
            )
            changed_input = (
                "Use at most four total requirement and compatibility IDs, omit duplicate "
                "semantics, and use one short verbatim quote per evidence link while preserving "
                "every supported semantic obligation."
            )
        draft = draft.model_copy(
            update={
                "request_id": f"CLICK_CONTRACT_REPAIR_{repair}",
                "response_id": f"CLICK_CONTRACT_REPAIR_RESPONSE_{repair}",
                "prompt_id": f"CLICK_CONTRACT_REPAIR_PROMPT_{repair}",
                "instruction": draft.instruction + " " + changed_input,
            }
        )
    frozen, measurement = measure(tokenizer, draft, RequirementContractProposal, label)
    provider = LocalGenerationProvider(backend=backend, archive=store.put_bytes)
    evidence = source_evidence(refs)
    provenance = Provenance(
        producer="feature_rl.requirements.ContractAuthoringService",
        producer_version=REVISION,
        created_at=datetime.now(timezone.utc),
        inputs=(
            refs["baseline"], refs["request_evidence"], refs["license_text"],
            recipe_ref, discovered.context.source, retrieval_ref,
        ),
        evidence=(evidence,),
    )
    inputs = ContractFinalizationInputs(
        visible_request=payloads["request_evidence"].decode(),
        allowed_requirement_ids=frozen.allowed_requirement_ids,
        entry_points=discovered.observation.entry_points,
        supported_observables=discovered.observation.supported_observables,
        runtime_discovery=discovered.context.source,
        allowed_changes=allowed,
        public_checks=(),
        episode_limits=store.get_artifact(recipe_ref, max_envelope_bytes=512 * 1024).limits,
        provenance_label="reconstructed_specification",
        visibility=Visibility.AUTHORING,
        provenance=provenance,
        costs=discovered.costs + (fixed_cost("authoring", "Retrieval and setup only; generation cost is appended by the service."),),
    )
    result = ContractAuthoringService(
        provider=provider, store=store,
        resolver=resolver(store, refs, discovered.context.source),
        revision=REVISION,
    ).generate(
        (
            GenerationCandidate(
                request=frozen, diagnosis=diagnosis, changed_input=changed_input
            ),
        ),
        inputs,
        sources,
        prior_journal_refs=prior_journals,
    )
    state = {
        "revision": REVISION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "refs": {name: value.model_dump(mode="json") for name, value in refs.items()},
        "prepared_recipe": recipe_ref.model_dump(mode="json"),
        "discovery_private_evidence": [
            item.model_dump(mode="json") for item in discovered.private_evidence
        ],
        "discovery": discovered.context.source.model_dump(mode="json"),
        "retrieval_receipt": retrieval_ref.model_dump(mode="json"),
        "sources": [item.model_dump(mode="json") for item in sources],
        "contract": result.contract_ref.model_dump(mode="json"),
        "contract_journals": [item.model_dump(mode="json") for item in result.journal_refs],
        "contract_generation_record": result.generation.record.model_dump(mode="json"),
        "contract_usage": result.generation.usage.model_dump(mode="json"),
        "contract_measurement": measurement,
        "native_calls": len(result.journal_refs),
        "license_prompted": False,
    }
    write_json(STATE_PATH, state)
    write_json(EVIDENCE / "frozen-contract.json", result.contract.model_dump(mode="json"))
    print(json.dumps({"status": "contract_frozen", "contract": state["contract"]}, sort_keys=True))


def scenario() -> None:
    state = json.loads(STATE_PATH.read_text())
    if state.get("native_calls") != 1 or state.get("scenario"):
        raise RuntimeError("scenario requires exactly one completed contract call")
    store = ArtifactStore(STORE_PATH, ActorRole.CONTROLLER)
    refs = {name: ref(value) for name, value in state["refs"].items()}
    discovery_ref = ref(state["discovery"])
    sources = tuple(GroundedSource.model_validate(item) for item in state["sources"])
    contract_ref = ref(state["contract"])
    contract_value = store.get_artifact(contract_ref, max_envelope_bytes=512 * 1024)
    tokenizer, backend = tokenizer_and_backend()
    draft = build_scenario_request(
        request_id="CLICK_SCENARIOS_1", response_id="CLICK_SCENARIOS_RESPONSE_1",
        prompt_id="CLICK_SCENARIOS_PROMPT_1", contract=contract_value,
        contract_ref=contract_ref, sources=sources, limits=limits(16_384), seed=0,
    )
    frozen, measurement = measure(tokenizer, draft, ScenarioPlanProposal, "scenario")
    provenance = Provenance(
        producer="feature_rl.scenarios.ScenarioAuthoringService",
        producer_version=REVISION,
        created_at=datetime.now(timezone.utc),
        inputs=(contract_ref, *refs.values(), discovery_ref, ref(state["retrieval_receipt"])),
        evidence=contract_value.provenance.evidence,
    )
    inputs = ScenarioFinalizationInputs(
        contract=contract_ref,
        supported_observables=("CLI exit code", "combined terminal output"),
        seed_policy=SeedPolicy(algorithm="PYTHONHASHSEED", seeds=(0,), same_cases_within_group=True),
        visibility=Visibility.EVALUATION,
        provenance=provenance,
        costs=(fixed_cost("scenario_design", "Generation cost is appended by the service."),),
    )
    result = ScenarioAuthoringService(
        provider=LocalGenerationProvider(backend=backend, archive=store.put_bytes),
        store=store, resolver=resolver(store, refs, discovery_ref), revision=REVISION,
    ).generate((GenerationCandidate(request=frozen),), inputs, sources)
    state.update(
        {
            "scenario": result.plan_ref.model_dump(mode="json"),
            "scenario_journals": [item.model_dump(mode="json") for item in result.journal_refs],
            "scenario_generation_record": result.generation.record.model_dump(mode="json"),
            "scenario_usage": result.generation.usage.model_dump(mode="json"),
            "scenario_measurement": measurement,
            "native_calls": 2,
        }
    )
    write_json(STATE_PATH, state)
    write_json(EVIDENCE / "scenario-plan.json", result.plan.model_dump(mode="json"))
    print(json.dumps({"status": "scenario_frozen", "scenario": state["scenario"]}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("contract", "scenario"))
    args = parser.parse_args()
    {"contract": contract, "scenario": scenario}[args.stage]()


if __name__ == "__main__":
    main()
