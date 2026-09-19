"""Reviewer diagnostics only: synthetic fixtures, no inference or application execution.

Run: PYTHONPATH=src .venv/bin/python -B docs/evidence/M2/review-authoring-round1/reproduce.py
The retained production driver is parsed, and selected controller functions are
executed with inert state/store doubles. No production state or private data is read.
"""
from __future__ import annotations

import ast
import atexit
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
TARGET = "c4ed70df6f3218ee2280b346eedb57945d0a44ab"

# The owner edits the shared workspace concurrently. Import the reviewed M2
# bytes from a temporary, review-owned snapshot; shared M0/M3 dependencies stay
# at their already reviewed local versions. This performs no checkout/index edit.
snapshot = tempfile.TemporaryDirectory(prefix="reviewed-source-", dir=HERE)
atexit.register(snapshot.cleanup)
snapshot_root = Path(snapshot.name)
paths = subprocess.check_output([
    "git", "ls-tree", "-r", "--name-only", TARGET, "--",
    "src/feature_rl/requirements", "src/feature_rl/scenarios", "src/feature_rl/generation",
], cwd=ROOT, text=True).splitlines()
snapshot_bytes = {}
for name in paths:
    data = subprocess.check_output(["git", "show", f"{TARGET}:{name}"], cwd=ROOT)
    snapshot_bytes[name] = data
    target = snapshot_root / Path(name).relative_to("src/feature_rl")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
import feature_rl
feature_rl.__path__.insert(0, str(snapshot_root))
fixture_namespace = {"__name__": "review_unit_fixture"}
fixture_data = subprocess.check_output(["git", "show", f"{TARGET}:tests/test_authoring.py"], cwd=ROOT)
exec(compile(fixture_data, f"{TARGET}:tests/test_authoring.py", "exec"), fixture_namespace)
f = SimpleNamespace(**fixture_namespace)

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, ArtifactRef, CostRecord, Visibility
from feature_rl.requirements import (
    AuthoringEvidenceResolver, AuthoringExhausted, ContractAuthoringService,
    GenerationCandidate, GroundedSource, RetrievalPolicy, build_contract_request,
)
from feature_rl.scenarios import ScenarioAuthoringService, build_scenario_request
from feature_rl.generation.provider import (
    GenerationProviderError, GenerationPublicationRecovery, _ArchivePayload,
)


def controller_functions(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:docs/evidence/M2/authoring-production/run.py"],
        cwd=ROOT, text=True,
    )
    parsed = ast.parse(source)
    selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                and node.name in {"ref", "fixed_cost", "scenario"}]
    namespace = {
        "json": json, "ArtifactRef": ArtifactRef, "CostRecord": CostRecord,
        "GroundedSource": GroundedSource, "canonical_json": canonical_json,
        "ActorRole": ActorRole,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "retained-driver", "exec"), namespace)
    return namespace, hashlib.sha256(source.encode()).hexdigest()


def exception_result(call):
    try:
        call()
    except Exception as error:
        return {"type": type(error).__name__, "message": str(error),
                "has_recovery": hasattr(error, "recovery"),
                "has_generation": hasattr(error, "generation"),
                "has_cost": hasattr(error, "cost")}
    return {"type": None}


def contract_service(store, provider, *, resolver=None):
    return ContractAuthoringService(
        provider=provider, store=store,
        resolver=resolver or f.FakeEvidenceResolver(f.sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    )


def candidate(index):
    return GenerationCandidate(
        request=f.contract_request(index),
        diagnosis=None if index == 1 else "Synthetic prior proposal was malformed.",
        changed_input=None if index == 1 else "Claimed change; actual semantic request is unchanged.",
    )


def real_resolver_fixture(store, suffix=""):
    request_text = f.sources()[0].text + suffix
    baseline_text = f.sources()[1].text + suffix + "\n"
    request_ref = store.put_bytes(request_text.encode(), "authoring-request", Visibility.AUTHORING)
    baseline_ref = store.put_bytes(
        f.source_archive({"src/click/core.py": baseline_text.encode()}),
        "source-archive", Visibility.AUTHORING,
    )
    discovery_text = f.bound_discovery_text(baseline=baseline_ref)
    discovery_ref = store.put_bytes(discovery_text.encode(), "click-runtime-discovery", Visibility.AUTHORING)
    sources = (
        GroundedSource(context_id="REQUEST", role="request", source=request_ref,
                       locator="authoring-request:whole", text=request_text,
                       provenance_label="reconstructed_specification"),
        GroundedSource(context_id="BASELINE", role="baseline", source=baseline_ref,
                       locator="src/click/core.py:1-1", text=baseline_text,
                       provenance_label="existing_obligation"),
        GroundedSource(context_id="M3_CLICK_DISCOVERY", role="baseline", source=discovery_ref,
                       locator="m3:click-runtime-discovery-v1", text=discovery_text,
                       provenance_label="existing_obligation"),
    )
    resolver = AuthoringEvidenceResolver(
        store=store, request=request_ref, baseline=baseline_ref, runtime_discovery=discovery_ref,
        public_checks=(), retrieval_policy=RetrievalPolicy(
            allowed_paths=("src/click/core.py",), max_archive_bytes=1024 * 1024,
            max_files=10, max_selected_bytes=1024, max_spans=2,
        ),
    )
    mapping = {f.REQUEST: sources[0], f.BASELINE: sources[1]}

    def link(value):
        target = mapping[value.source]
        return value.model_copy(update={"source": target.source, "locator": target.locator})

    proposal = f.contract_proposal()
    proposal = proposal.model_copy(update={
        "requirements": tuple(r.model_copy(update={"evidence": tuple(map(link, r.evidence))})
                              for r in proposal.requirements),
        "compatibility_obligations": tuple(r.model_copy(update={"evidence": tuple(map(link, r.evidence))})
                                           for r in proposal.compatibility_obligations),
        "ambiguities": tuple(a.model_copy(update={"evidence": tuple(map(link, a.evidence))})
                             for a in proposal.ambiguities),
    })
    inputs = f.contract_inputs().model_copy(update={
        "visible_request": request_text, "runtime_discovery": discovery_ref, "public_checks": (),
        "provenance": f.provenance(request_ref, baseline_ref, discovery_ref),
    })
    request = build_contract_request(
        request_id="REQ_1", response_id="RESP_1", prompt_id="PROMPT_1", sources=sources,
        allowed_requirement_ids=inputs.allowed_requirement_ids, entry_points=inputs.entry_points,
        supported_observables=inputs.supported_observables, allowed_changes=inputs.allowed_changes,
        limits=f.generation_limits(), seed=0,
    )
    scenarios = f.scenario_proposal().model_copy(update={"scenarios": tuple(
        s.model_copy(update={"oracle_origin": link(s.oracle_origin)})
        for s in f.scenario_proposal().scenarios
    )})
    return SimpleNamespace(sources=sources, resolver=resolver, proposal=proposal,
                           inputs=inputs, request=request, scenarios=scenarios)


results = {"scope": "unit_diagnostic", "native_calls": 0, "docker_calls": 0,
           "tokenizer_calls": 0, "private_or_production_state_reads": 0, "cases": {}}
driver, driver_hash = controller_functions("d267208")
results["driver_revision"] = "d267208"
results["driver_sha256"] = driver_hash
driver["STATE_PATH"] = SimpleNamespace(read_text=lambda: json.dumps({"native_calls": 2}))
results["cases"]["driver_repaired_contract"] = exception_result(driver["scenario"])
state = {"native_calls": 1, "refs": {}, "discovery": f.DISCOVERY.model_dump(mode="json"),
         "sources": [source.model_dump(mode="json") for source in f.sources()]}
driver["STATE_PATH"] = SimpleNamespace(read_text=lambda: json.dumps(state))
driver["STORE_PATH"] = None
driver["ArtifactStore"] = lambda *args: object()
results["cases"]["driver_grounded_source_json"] = exception_result(driver["scenario"])
results["cases"]["driver_cost_category"] = exception_result(
    lambda: driver["fixed_cost"]("scenario_design", "Synthetic fixture")
)
fixed_driver, fixed_driver_hash = controller_functions("aa75086")
results["driver_fix_revision"] = "aa75086"
results["driver_fix_sha256"] = fixed_driver_hash
results["cases"]["driver_fixed_earlier_success_states"] = {}
for calls in (1, 2):
    fixed_driver["STATE_PATH"] = SimpleNamespace(read_text=lambda calls=calls: json.dumps({
        "native_calls": calls, "candidate_repairs_used": calls, "candidate_repair_budget": 4,
    }))
    results["cases"]["driver_fixed_earlier_success_states"][str(calls)] = exception_result(fixed_driver["scenario"])
results["cases"]["driver_fixed_source_and_cost"] = {
    "strict_source_json": GroundedSource.model_validate_json(canonical_json(f.sources()[0].model_dump(mode="json"))) == f.sources()[0],
    "cost_category": fixed_driver["fixed_cost"]("authoring", "Synthetic fixture").category,
}

with tempfile.TemporaryDirectory(prefix="diagnostic-cas-", dir=HERE) as temp:
    root = Path(temp)
    store = ArtifactStore(root / "provider-recovery", ActorRole.CONTROLLER)
    success = f.provider_result(f.contract_proposal())
    record = success.record.model_copy(update={"success": False, "publication_complete": False,
                                              "error_code": "ArchivePublicationError"})
    recovery = GenerationPublicationRecovery(
        attempt_id=record.attempt_id, recorded_at=record.recorded_at, request_id=record.request_id,
        response_id=record.response_id,
        payloads=(_ArchivePayload("response", b"fixture retained response", "generation-response", Visibility.AUTHORING),),
        published_refs=(), generation_succeeded=True, underlying_error_code=None,
        underlying_error=None, publication_error="fixture disk failure", cost=success.cost,
        response={"fixture_only": True}, usage_observation={"fixture_only": True},
    )
    failure = GenerationProviderError("fixture pending publication", record, cost=success.cost,
                                      recovery=recovery, response={"fixture_only": True})
    provider = f.FakeProvider((failure, f.provider_result(f.contract_proposal(), 2)))
    accepted = contract_service(store, provider).generate(
        (candidate(1), candidate(2)), f.contract_inputs(), f.sources()
    )
    journal = json.loads(store.get_bytes(accepted.journal_refs[0]))
    results["cases"]["provider_publication_recovery_dropped"] = {
        "provider_calls": len(provider.calls), "accepted_attempt": accepted.generation.record.attempt_id,
        "first_generation_succeeded": journal["generation_record"]["generation_succeeded"],
        "first_publication_complete": journal["generation_record"]["publication_complete"],
        "journal_has_recovery": "recovery" in journal,
    }

    store = ArtifactStore(root / "journal-recovery", ActorRole.CONTROLLER)
    original = store.put_bytes
    def fail_journal(data, kind, visibility):
        if kind == "contract-authoring-journal":
            raise OSError("fixture rejection-journal disk failure")
        return original(data, kind, visibility)
    store.put_bytes = fail_journal
    provider = f.FakeProvider((f.provider_result({"malformed": True}),))
    results["cases"]["rejected_journal_publication"] = exception_result(
        lambda: contract_service(store, provider).generate((candidate(1),), f.contract_inputs(), f.sources())
    )
    results["cases"]["rejected_journal_publication"]["provider_calls"] = len(provider.calls)

    store = ArtifactStore(root / "request-binding", ActorRole.CONTROLLER)
    fixture = real_resolver_fixture(store)
    changed_inputs = fixture.inputs.model_copy(update={
        "visible_request": "DIAGNOSTIC UNRELATED REQUEST: delete every CLI command.",
        "provenance_label": "historical_request",
    })
    provider = f.FakeProvider((f.provider_result(fixture.proposal),))
    accepted = contract_service(store, provider, resolver=fixture.resolver).generate(
        (GenerationCandidate(request=fixture.request),), changed_inputs, fixture.sources
    )
    results["cases"]["visible_request_and_provenance_not_joined"] = {
        "accepted": True, "visible_request": accepted.contract.visible_request,
        "source_request": fixture.sources[0].text,
        "contract_provenance_label": accepted.contract.provenance_label,
        "source_provenance_label": fixture.sources[0].provenance_label,
    }

    other = real_resolver_fixture(store, " DIAGNOSTIC SECOND CANDIDATE")
    scenario_request = build_scenario_request(
        request_id="SCENARIO_REQ", response_id="SCENARIO_RESP", prompt_id="SCENARIO_PROMPT",
        contract=accepted.contract, contract_ref=accepted.contract_ref, sources=other.sources,
        limits=f.generation_limits(), seed=0,
    )
    scenario_inputs = f.scenario_inputs(accepted.contract_ref).model_copy(update={
        "supported_observables": ("object identity",),
        "provenance": f.provenance(accepted.contract_ref, *(s.source for s in other.sources)),
    })
    scenario_proposal = other.scenarios.model_copy(update={"scenarios": tuple(
        s.model_copy(update={"observations": ("object identity",)}) for s in other.scenarios.scenarios
    )})
    provider = f.FakeProvider((f.provider_result(scenario_proposal),))
    planned = ScenarioAuthoringService(
        provider=provider, store=store, resolver=other.resolver,
        revision="2" * 40, evidence_scope="unit_diagnostic",
    ).generate((GenerationCandidate(request=scenario_request),), scenario_inputs, other.sources)
    results["cases"]["scenario_source_and_capability_not_joined"] = {
        "accepted": True,
        "oracle_source_in_contract_inputs": planned.plan.scenarios[0].oracle_origin.source in accepted.contract.provenance.inputs,
        "accepted_observations": planned.plan.scenarios[0].observations,
        "discovery_supported_observables": json.loads(other.sources[-1].text)["supported_observables"],
    }

    store = ArtifactStore(root / "budgets", ActorRole.CONTROLLER)
    provider = f.FakeProvider((f.provider_result({"malformed": True}),
                              f.provider_result({"malformed": True}, 2),
                              f.provider_result(f.contract_proposal(), 3)))
    authored = contract_service(store, provider).generate(
        tuple(candidate(i) for i in (1, 2, 3)), f.contract_inputs(), f.sources()
    )
    scenarios = []
    for i in (1, 2, 3):
        request = build_scenario_request(
            request_id=f"SCENARIO_REQ_{i}", response_id=f"SCENARIO_RESP_{i}", prompt_id=f"SCENARIO_PROMPT_{i}",
            contract=authored.contract, contract_ref=authored.contract_ref, sources=f.sources(),
            limits=f.generation_limits(), seed=0,
        )
        scenarios.append(GenerationCandidate(request=request, diagnosis=None if i == 1 else "Fixture failure",
                                              changed_input=None if i == 1 else "Claimed change"))
    scenario_provider = f.FakeProvider((f.provider_result({"malformed": True}),
                                       f.provider_result({"malformed": True}, 2),
                                       f.provider_result(f.scenario_proposal(), 3)))
    ScenarioAuthoringService(
        provider=scenario_provider, store=store, resolver=f.FakeEvidenceResolver(f.sources()),
        revision="2" * 40, evidence_scope="unit_diagnostic",
    ).generate(tuple(scenarios), f.scenario_inputs(authored.contract_ref), f.sources())
    def semantic_input(request):
        return request.model_dump(exclude={"request_id", "response_id", "prompt_id"})
    results["cases"]["stage_and_total_repairs"] = {
        "contract_calls": len(provider.calls), "scenario_calls": len(scenario_provider.calls),
        "new_authoring_repairs": 4, "prior_click_environment_repairs": 1,
        "candidate_repairs_total": 5, "specified_candidate_limit": 4,
        "contract_repair_semantic_input_unchanged": semantic_input(provider.calls[0][0]) == semantic_input(provider.calls[1][0]),
    }

results["recorded_at"] = datetime.now(timezone.utc).isoformat()
results["product_revision"] = TARGET
results["product_sha256"] = {
    name: hashlib.sha256(data).hexdigest() for name, data in snapshot_bytes.items()
}
results["fixture_sha256"] = hashlib.sha256(fixture_data).hexdigest()
print(json.dumps(results, indent=2, sort_keys=True))
