"""Pinned, synthetic M2 recovery diagnostics; no native/model/application execution.

The production provider controller runs against the existing inert test backend
and event-stream runner. All CAS objects are temporary review fixtures. This is
not another model call or feature-construction attempt.
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
TARGET = "7b8974637e9ffc5bab79f9590b90c7021228d473"
tempfile.tempdir = str(HERE)

def git_bytes(path):
    return subprocess.check_output(["git", "show", f"{TARGET}:{path}"], cwd=ROOT)

snapshot = tempfile.TemporaryDirectory(prefix="pinned-source-", dir=HERE)
atexit.register(snapshot.cleanup)
snapshot_root = Path(snapshot.name)
paths = subprocess.check_output([
    "git", "ls-tree", "-r", "--name-only", TARGET, "--", "src/feature_rl/requirements",
    "src/feature_rl/scenarios", "src/feature_rl/generation",
], cwd=ROOT, text=True).splitlines()
hashes = {}
for name in paths:
    data = git_bytes(name)
    hashes[name] = hashlib.sha256(data).hexdigest()
    path = snapshot_root / Path(name).relative_to("src/feature_rl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
sys.path.insert(0, str(ROOT / "src"))
import feature_rl
feature_rl.__path__.insert(0, str(snapshot_root))

def fixture(path, name):
    data = git_bytes(path)
    hashes[path] = hashlib.sha256(data).hexdigest()
    namespace = {"__name__": name, "__file__": str(ROOT / path)}
    exec(compile(data, f"{TARGET}:{path}", "exec"), namespace)
    return SimpleNamespace(**namespace)

f = fixture("tests/test_authoring.py", "review_authoring_fixture")
g = fixture("tests/test_generation.py", "review_generation_fixture")
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, Visibility
from feature_rl.generation import LocalGenerationProvider
from feature_rl.generation.provider import GenerationProviderError
from feature_rl.requirements import AuthoringEvidenceResolver, GenerationCandidate, GroundedSource

# Reuse the round-one real-resolver fixture definition without executing that
# diagnostic or importing its old product snapshot.
old = ast.parse((HERE.parent / "review-authoring-round1/reproduce.py").read_text())
definition = next(node for node in old.body if isinstance(node, ast.FunctionDef)
                  and node.name == "real_resolver_fixture")
namespace = dict(vars(f)) | {"f": f, "SimpleNamespace": SimpleNamespace}
exec(compile(ast.Module(body=[definition], type_ignores=[]), "reused_fixture", "exec"), namespace)
real_resolver_fixture = namespace["real_resolver_fixture"]

def events_for(request, content, malformed=False):
    values = [json.loads(line) for line in g.worker_events().splitlines()]
    for event in values:
        event.update(request_id=request.request_id, response_id=request.response_id,
                     prompt_id=request.prompt_id)
        if event["event"] == "input_accepted":
            event["max_input_tokens"] = request.limits.input_tokens
        if event["event"] == "completed":
            event["max_output_tokens"] = request.limits.output_tokens
            event["output_text"] = "{" if malformed else json.dumps({
                "response_id": request.response_id,
                "source_ids": [item.context_id for item in request.contexts],
                "requirement_ids": list(request.allowed_requirement_ids),
                "content": content.model_dump(mode="json"),
            })
    return ("\n".join(json.dumps(value) for value in values) + "\n").encode()

class CapturingProvider:
    def __init__(self, provider):
        self.provider = provider
        self.failure = None
    def generate(self, *args):
        try:
            return self.provider.generate(*args)
        except GenerationProviderError as error:
            self.failure = error
            raise

def failing_archive_provider(store, request, content, malformed=False):
    once = []
    def archive(data, kind, visibility):
        if kind == "generation-response" and not once:
            once.append(True)
            raise OSError("synthetic one-time archive failure")
        return store.put_bytes(data, kind, visibility)
    runner = g.FakeRunner(stdout=events_for(request, content, malformed))
    return CapturingProvider(LocalGenerationProvider(
        backend=g.FakeBackend(), archive=archive, runner=runner,
    )), runner

def error_of(call):
    try:
        call()
    except Exception as error:
        return {"type": type(error).__name__, "message": str(error),
                "has_recovery": getattr(error, "recovery", None) is not None}, error
    return {"type": None}, None

def contract_service(store, provider):
    return f.ContractAuthoringService(provider=provider, store=store,
        resolver=f.FakeEvidenceResolver(f.sources()), revision=TARGET, evidence_scope="unit_diagnostic")

results = {"product": TARGET, "scope": "unit_diagnostic", "native_calls": 0,
           "docker_calls": 0, "tokenizer_calls": 0, "production_or_private_reads": 0, "cases": {}}
with tempfile.TemporaryDirectory(prefix="fixture-cas-", dir=HERE) as temp:
    root = Path(temp)
    for stage in ("contract", "scenario"):
        store = ArtifactStore(root / stage, ActorRole.CONTROLLER)
        if stage == "contract":
            request = f.contract_request()
            proposal, inputs = f.contract_proposal(), f.contract_inputs()
            make_service = lambda provider: contract_service(store, provider)
        else:
            contract = f.finalized_contract()
            contract_ref = store.put_artifact(contract)
            request = f.build_scenario_request(request_id="SCENARIO_REQ", response_id="SCENARIO_RESP",
                prompt_id="SCENARIO_PROMPT", contract=contract, contract_ref=contract_ref,
                sources=f.sources(), limits=f.generation_limits(), seed=0)
            proposal, inputs = f.scenario_proposal(), f.scenario_inputs(contract_ref)
            make_service = lambda provider: f.ScenarioAuthoringService(provider=provider, store=store,
                resolver=f.FakeEvidenceResolver(f.sources()), revision=TARGET, evidence_scope="unit_diagnostic")
        provider, runner = failing_archive_provider(store, request, proposal)
        outcome, caught = error_of(lambda: make_service(provider).generate(
            (GenerationCandidate(request=request),), inputs, f.sources()))
        assert isinstance(caught, GenerationProviderError) and caught.recovery is not None
        recovered = caught.replay_result(store.put_bytes)
        changed = request.model_copy(update={"instruction": request.instruction + " DIAGNOSTIC CHANGED REQUEST."})
        no_model = f.FakeProvider(())
        accepted = make_service(no_model).generate(
            (GenerationCandidate(request=changed),), inputs, f.sources(), recovered_result=recovered)
        archived_request = json.loads(store.get_bytes(recovered.record.archives["request"]))
        journal = json.loads(store.get_bytes(accepted.journal_refs[-1]))
        results["cases"][f"{stage}_recovered_request_mismatch"] = {
            "accepted": True, "provider_fixture_runner_calls": len(runner.calls),
            "recovery_provider_calls": len(no_model.calls),
            "journal_request_hash": journal["request_sha256"],
            "archived_request_hash": hashlib.sha256(canonical_json(archived_request)).hexdigest(),
            "archived_request_matches_resumed": archived_request == changed.model_dump(mode="json"),
            "original_recovery_preserved": outcome["has_recovery"],
            "recovered_archive_count": len(recovered.record.archives),
        }
        altered_cost = recovered.cost.model_copy(update={"wall_seconds": 0.0, "cpu_seconds": 0.0})
        altered = recovered.model_copy(update={"cost": altered_cost})
        reused = make_service(no_model).generate(
            (GenerationCandidate(request=request),), inputs, f.sources(), recovered_result=altered)
        artifact = reused.contract if stage == "contract" else reused.plan
        archived_cost = json.loads(store.get_bytes(recovered.record.archives["cost"]))
        results["cases"][f"{stage}_recovered_cost_mismatch"] = {
            "accepted": True, "artifact_wall_seconds": artifact.costs[-1].wall_seconds,
            "archived_wall_seconds": archived_cost["wall_seconds"],
            "artifact_cpu_seconds": artifact.costs[-1].cpu_seconds,
            "archived_cpu_seconds": archived_cost["cpu_seconds"],
        }

    store = ArtifactStore(root / "failed-generation", ActorRole.CONTROLLER)
    request = f.contract_request()
    provider, runner = failing_archive_provider(store, request, f.contract_proposal(), malformed=True)
    outcome, caught = error_of(lambda: contract_service(store, provider).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources()))
    inner = provider.failure
    assert inner is not None and inner.recovery is not None and not inner.record.generation_succeeded
    results["cases"]["failed_generation_pending_publication_lost"] = outcome | {
        "provider_fixture_runner_calls": len(runner.calls),
        "inner_has_recovery": inner.recovery is not None,
        "inner_publication_complete": inner.record.publication_complete,
        "inner_archives": list(inner.record.archives),
        "inner_recovery_payload_count": len(inner.recovery.payloads),
        "returned_has_inner_error": getattr(caught, "__cause__", None) is inner or getattr(caught, "__context__", None) is inner,
    }

    store = ArtifactStore(root / "public-check", ActorRole.CONTROLLER)
    base = real_resolver_fixture(store)
    check_text = "DIAGNOSTIC public compatibility check."
    check = store.put_bytes(check_text.encode(), "public-check", Visibility.PUBLIC)
    sources = base.sources + (GroundedSource(context_id="PUBLIC", role="public_check", source=check,
        locator="artifact:whole", text=check_text, provenance_label="existing_obligation"),)
    provenance = base.inputs.provenance.model_copy(update={"inputs": base.inputs.provenance.inputs + (check,)})
    inputs = base.inputs.model_copy(update={"public_checks": (check,), "provenance": provenance})
    contract = f.ContractFinalizer().finalize(base.proposal, inputs, sources)
    contract_ref = store.put_artifact(contract)
    resolver = AuthoringEvidenceResolver(store=store, request=base.sources[0].source,
        baseline=base.sources[1].source, runtime_discovery=base.sources[2].source,
        public_checks=(check,), retrieval_policy=base.resolver.retrieval_policy)
    assert resolver.resolve(sources) == sources
    request = f.build_scenario_request(request_id="PUBLIC_CHECK_REQ", response_id="PUBLIC_CHECK_RESP",
        prompt_id="PUBLIC_CHECK_PROMPT", contract=contract, contract_ref=contract_ref,
        sources=sources, limits=f.generation_limits(), seed=0)
    provider = f.FakeProvider(())
    service = f.ScenarioAuthoringService(provider=provider, store=store, resolver=resolver,
        revision=TARGET, evidence_scope="unit_diagnostic")
    outcome, _ = error_of(lambda: service.generate((GenerationCandidate(request=request),),
        f.scenario_inputs(contract_ref), sources))
    results["cases"]["admitted_public_check_rejected"] = outcome | {
        "exact_public_check_in_contract": check in contract.public_checks and check in contract.provenance.inputs,
        "resolver_accepted_all_sources": True, "provider_calls": len(provider.calls),
    }

results["recorded_at"] = datetime.now(timezone.utc).isoformat()
results["source_sha256"] = hashes
print(json.dumps(results, indent=2, sort_keys=True))
