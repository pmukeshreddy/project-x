"""Focused review fixtures for new recovery-validator compatibility boundaries.

No native inference, tokenizer, Docker, application, or production/private reads.
Reuse the existing review fixture setup and pin only its M2 source target forward.
"""
from pathlib import Path
import hashlib

prior = Path(__file__).resolve().parent.parent / "review-authoring-round2/reproduce.py"
prior_source = prior.read_text()
bootstrap_sha256 = hashlib.sha256(prior_source.encode()).hexdigest()
prefix = prior_source.split('\nresults = {', 1)[0]
prefix = prefix.replace('TARGET = "7b8974637e9ffc5bab79f9590b90c7021228d473"',
                        'TARGET = "d64f172efb2d50ce9303725a08ed2fcfd9f4a65f"')
exec(compile(prefix, str(prior) + ":fixture-setup-only", "exec"), globals())

results = {"product": TARGET, "scope": "unit_diagnostic", "native_calls": 0,
           "docker_calls": 0, "tokenizer_calls": 0, "production_or_private_reads": 0,
           "bootstrap_sha256": bootstrap_sha256, "cases": {}}
with tempfile.TemporaryDirectory(prefix="fixture-cas-", dir=HERE) as temp:
    root = Path(temp)
    # Verify the actual full provider archive can still resume, and the prior
    # changed-request/cost cases reject before another provider call.
    store = ArtifactStore(root / "full-success", ActorRole.CONTROLLER)
    request, proposal = f.contract_request(), f.contract_proposal()
    provider, runner = failing_archive_provider(store, request, proposal)
    _, pending = error_of(lambda: contract_service(store, provider).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources()))
    assert isinstance(pending, GenerationProviderError)
    recovered = pending.replay_result(store.put_bytes)
    no_model = f.FakeProvider(())
    accepted = contract_service(store, no_model).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources(), recovered_result=recovered)
    changed = request.model_copy(update={"instruction": request.instruction + " changed"})
    request_result, _ = error_of(lambda: contract_service(store, no_model).generate(
        (GenerationCandidate(request=changed),), f.contract_inputs(), f.sources(), recovered_result=recovered))
    cost_result, _ = error_of(lambda: contract_service(store, no_model).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources(),
        recovered_result=recovered.model_copy(update={"cost": recovered.cost.model_copy(update={"wall_seconds": 0.0})})))
    results["cases"]["full_success_and_original_mismatches"] = {
        "accepted_exact_result": accepted.contract_ref.kind == "RequirementContract",
        "request_mismatch": request_result, "cost_mismatch": cost_result,
        "provider_fixture_runner_calls": len(runner.calls), "recovery_provider_calls": len(no_model.calls),
    }

    store = ArtifactStore(root / "full-failure", ActorRole.CONTROLLER)
    provider, runner = failing_archive_provider(store, request, proposal, malformed=True)
    _, pending = error_of(lambda: contract_service(store, provider).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources()))
    assert isinstance(pending, GenerationProviderError) and pending.recovery is not None
    recovered_error = pending.replay_error(store.put_bytes)
    no_model = f.FakeProvider(())
    outcome, rejected = error_of(lambda: contract_service(store, no_model).generate(
        (GenerationCandidate(request=request),), f.contract_inputs(), f.sources(), recovered_error=recovered_error))
    results["cases"]["full_failure_replay"] = outcome | {
        "journal_count": len(getattr(rejected, "journal_refs", ())),
        "archive_names": list(recovered_error.record.archives),
        "provider_fixture_runner_calls": len(runner.calls), "recovery_provider_calls": len(no_model.calls),
    }

    for kind in ("registration", "preflight"):
        store = ArtifactStore(root / kind, ActorRole.CONTROLLER)
        first = []
        failure_kind = "generation-attempt" if kind == "registration" else "generation-preflight"
        def archive(data, archive_kind, visibility):
            if archive_kind == failure_kind and not first:
                first.append(True)
                raise OSError("diagnostic one-time publication failure")
            return store.put_bytes(data, archive_kind, visibility)
        small_request = request if kind == "registration" else request.model_copy(update={
            "instruction": request.instruction + " DIAGNOSTIC " * 1000,
            "limits": request.limits.model_copy(update={"stdin_bytes": 4096}),
        })
        backend, runner = g.NeverVerifiedBackend(), g.FakeRunner()
        provider = LocalGenerationProvider(backend=backend, archive=archive, runner=runner)
        _, pending = error_of(lambda: contract_service(store, provider).generate(
            (GenerationCandidate(request=small_request),), f.contract_inputs(), f.sources()))
        assert isinstance(pending, GenerationProviderError) and pending.recovery is not None
        replayed = pending.replay_error(store.put_bytes)
        no_model = f.FakeProvider(())
        outcome, _ = error_of(lambda: contract_service(store, no_model).generate(
            (GenerationCandidate(request=small_request),), f.contract_inputs(), f.sources(), recovered_error=replayed))
        results["cases"][kind + "_replay"] = outcome | {
            "archive_names": list(replayed.record.archives),
            "publication_complete": replayed.record.publication_complete,
            "original_error_code": replayed.record.error_code,
            "backend_verifications": backend.verify_calls, "fixture_runner_calls": len(runner.calls),
            "recovery_provider_calls": len(no_model.calls),
        }

    # A legal retained raw stream is enlarged only by ordinary token-event
    # repetition under the existing request's 4096-token / 1MiB output limits.
    store = ArtifactStore(root / "response-size", ActorRole.CONTROLLER)
    large_ids = request.model_copy(update={"request_id": "R" * 128,
        "response_id": "S" * 128, "prompt_id": "P" * 128})
    stream = [json.loads(line) for line in events_for(large_ids, proposal).splitlines()]
    token = next(event for event in stream if event["event"] == "token")
    count = 1650
    expanded = []
    for event in stream:
        if event["event"] == "token":
            expanded.extend(token | {"position": i} for i in range(1, count + 1))
        else:
            if event["event"] == "completed":
                event["output_tokens"] = count
            expanded.append(event)
    raw = ("\n".join(json.dumps(event) for event in expanded) + "\n").encode()
    assert len(raw) <= large_ids.limits.output_bytes and count < large_ids.limits.output_tokens
    failed = []
    def archive_large(data, kind, visibility):
        if kind == "generation-response" and not failed:
            failed.append(True)
            raise OSError("diagnostic one-time response publication failure")
        return store.put_bytes(data, kind, visibility)
    runner = g.FakeRunner(stdout=raw)
    provider = LocalGenerationProvider(backend=g.FakeBackend(), archive=archive_large, runner=runner)
    _, pending = error_of(lambda: contract_service(store, provider).generate(
        (GenerationCandidate(request=large_ids),), f.contract_inputs(), f.sources()))
    assert isinstance(pending, GenerationProviderError) and pending.record.generation_succeeded
    recovered = pending.replay_result(store.put_bytes)
    receipt = store.get_bytes(recovered.record.archives["response"], max_payload_bytes=2 * 1024 * 1024,
                              max_envelope_bytes=3 * 1024 * 1024)
    no_model = f.FakeProvider(())
    outcome, _ = error_of(lambda: contract_service(store, no_model).generate(
        (GenerationCandidate(request=large_ids),), f.contract_inputs(), f.sources(), recovered_result=recovered))
    results["cases"]["response_receipt_size"] = outcome | {
        "raw_stream_bytes_before_runner_identity_fill": len(raw),
        "response_receipt_bytes": len(receipt), "declared_output_bytes": large_ids.limits.output_bytes,
        "output_token_count": count, "declared_output_tokens": large_ids.limits.output_tokens,
        "publication_complete": recovered.record.publication_complete,
        "provider_fixture_runner_calls": len(runner.calls), "recovery_provider_calls": len(no_model.calls),
    }

results["recorded_at"] = datetime.now(timezone.utc).isoformat()
results["source_sha256"] = hashes
print(json.dumps(results, indent=2, sort_keys=True))
