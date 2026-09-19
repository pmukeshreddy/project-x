# Independent M2 provider review

Reviewed `e1d4e72814ec634bba1ac4b88f0b4a4fbe1776aa` through `8a4637552b87dbf5e7bfe57b0a843c3a59327dad`, using the complete supplied diff and actual generation implementation, tests, M0/M1 interfaces and upstream code, provider brief/report, and preserved evidence. The coordinator-only ledger, admission and M3 preparation changes in the supplied diff are context rather than M2 product scope. Reviewer: `/root/review_m2_provider`, 2026-09-19.

**Specification compliance: FAIL for the provider slice.** The implementation has the selected real backend and several sound boundaries, but process cleanup on exceptions, strict event validation, JSON schema transport and failure publication do not satisfy the brief. These are implementable local defects, separate from the pending current-version native check.

**Implementation quality: CHANGES REQUIRED.** The small provider/worker/runner separation is understandable, and the evidence limitations are candidly disclosed. The acceptance tests miss significant negative paths; their abbreviated successful event fixture actually depends on acceptance of incomplete protocol events. Return the findings below to the M2 owner, then independently review the corrections before native verification or downstream use.

## Prioritized findings

### F1 — P1: Monitoring exceptions bypass cleanup; observation loss can disappear from a successful receipt

Locations: `src/feature_rl/generation/runner.py:143`, `:154`, `:174`, `:180`; related failure classification at `src/feature_rl/generation/provider.py:379` and `:402`.

Once `Popen` succeeds, the monitoring loop has no enclosing `try/finally` that kills and reaps its process group. An exception from footprint observation, capture-file inspection or cleanup exits before the later cleanup block. The provider catches an ordinary exception, has no `ProcessOutcome`, and records the pre-execution-style “Generation did not start”/unknown-cost path even though a child may already be running. Controller interruption also bypasses this path.

The narrow diagnostic injects `OSError` into `_footprint` while the real runner executes only trusted Python `time.sleep(3)`, under a 0.25-second wall limit. `run()` raises while the child is still alive and its group is present. The reviewer then explicitly kills/reaps that child and verifies group absence; no probe process was left running.

There is a second fail-open observation path: one or two `None` samples are discarded, and a later successful sample resets the counter. A trusted 0.08-second sleeper with one injected lost observation returns `termination="process_exit"`, exit 0, three valid samples and no recorded observation failure. The brief requires failed watchdog observations to reject, and the receipt cannot disclose the actual gap.

Required correction: own cleanup from the moment a child exists, execute it on every exception/interruption path, and retain the partial outcome and known measurements when failure escapes. Treat observation failure as a failed call under the approved policy and record it. Preserve bounded cleanup and verified group absence; do not convert an observation error into success. This finding concerns a lost watchdog/cleanup boundary, not the already disclosed between-sample memory overshoot.

Evidence: `monitor_exception_cleanup` and `recovered_monitor_loss` in [provider-review-diagnostics.json](../evidence/M2/review/provider-review-diagnostics.json).

### F2 — P1: Valid structured JSON cannot satisfy the advertised M0/StrictModel output interface

Location: `src/feature_rl/generation/provider.py:280`.

After JSON decoding, `_parse_envelope` passes an ordinary Python dictionary to `schema.model_validate`. M0's strict models distinguish Python transport from JSON transport: tuple fields require tuples in Python, enum fields require enum instances, and UTC timestamps require datetime objects. The model can only emit their JSON array/string representations. Consequently a valid M0-shaped response is rejected even when it conforms to the exact emitted JSON Schema.

The diagnostic schema has `values: tuple[str, ...]` and `visibility: Visibility`. `{"values":["valid"],"visibility":"authoring"}` passes that schema's `model_validate_json`, but the provider rejects it with “model response content violates its strict schema.” M0 requirement/scenario-related models extensively use these types, so this blocks the intended authoring integration rather than an unusual output format.

Required correction: retain duplicate-key/nonfinite/envelope checks and validate the content through the documented strict JSON transport. Add a valid nested proposal/M0-style transport case plus invalid-field counterparts; do not relax M0 strictness or replace tuples/enums merely to fit the parser.

Evidence: `strict_json_transport` in [provider-review-diagnostics.json](../evidence/M2/review/provider-review-diagnostics.json).

### F3 — P1: The event parser accepts incomplete and contradictory backend/protocol evidence

Locations: `src/feature_rl/generation/provider.py:100`, `:119`, `:148`, `:169`; abbreviated success fixture at `tests/test_generation.py:331`.

Event names/order and selected fields are checked, but there is no exact schema for each event. The parser ignores the worker's config/tokenizer/weights hashes, dependency versions, offline/remote-code flags, completion response ID, token text fragments and several required measurements; unknown fields also pass. The test success fixture omits many fields emitted by the actual worker, so it does not test the advertised complete protocol.

Independent diagnostic calls all return `record.success=true` for these separate changes: an identity event with absent hash/dependency/offline fields; all-zero config/tokenizer/weight hashes with `dependency_versions={"mlx":"0.0.0"}`, `local_files_only=false`, `remote_code=true` and an extra field; a completion with `response_id="DIFFERENT_RESPONSE"`; and a token with `selected_model_logprob=2.0`. That last value is finite but cannot be a log probability. These are injected unit streams, not evidence that the real pinned worker emitted such values. They demonstrate that this boundary would accept drift or internally contradictory evidence despite the explicit refusal contract.

Required correction: validate complete versioned event schemas with exact types and extra-field rejection, then compare identity/configuration fields with the verified backend/request and check usage domains. Build the success fixture from the complete v2 protocol and mutate one required field at a time. Keep selected model scores distinct from unavailable behavior log probabilities.

Evidence: `event_protocol` in [provider-review-diagnostics.json](../evidence/M2/review/provider-review-diagnostics.json).

### F4 — P2: Archive publication failure discards the recoverable call record and known costs

Locations: `src/feature_rl/generation/provider.py:318`, `:457`, `:468`.

All publication happens after the subprocess and validation, outside the provider's exception-to-record handling. If the archive callback fails, the caller receives its raw exception rather than `GenerationProviderError` with a call record. The request is not durably registered before execution, and the returned outcome/usage/cost plus already published references are lost from the API. Retrying generation to recover this state would incur a new call rather than finishing publication of the original attempt.

The unit diagnostic lets the existing test runner return a complete response, publishes `generation-request`, then raises `OSError` while publishing `generation-response`. The API raises that `OSError` with no `record`; no response, cost or status artifact has been published, despite known diagnostic wall/CPU/token measurements. This is a publication-failure reproducer, not an inference receipt.

Required correction: establish an attributable attempt before execution and preserve bounded response/measurement data and partial publication references until publication completes or recovery is explicitly handed back. An unavailable store cannot be forced to write, but that must remain an explicit recoverable archival failure with the known outcome, not lost call evidence or an automatic new inference.

Evidence: [archive-failure-diagnostic.json](../evidence/M2/review/archive-failure-diagnostic.json).

### F5 — P2: Envelope failures use the successful usage-archive shape

Locations: `src/feature_rl/generation/provider.py:377` and `:448`; promised failure semantics in `docs/interfaces-M2.md:74`.

`usage` is assigned after event parsing, before envelope/content validation. If the envelope cites an unknown requirement or its content is malformed, the call correctly fails, but the usage artifact still takes the successful `GenerationUsage` branch. It omits `accepted_response_usage=false`, contrary to the documented failure-archive interface. The diagnostic unknown-requirement response demonstrates this exact combination: failure status beside the successful usage shape.

Required correction: distinguish validated token observations from accepted response usage on every failed call, while preserving all known counts, IDs and scores for cost accounting. A valid event stream does not validate its model response. Keep the failure status and usage acceptance indicator consistent.

Evidence: `failed_envelope_usage` in [provider-review-diagnostics.json](../evidence/M2/review/provider-review-diagnostics.json).

## Verified strengths and evidence boundaries

Source inspection confirms an explicitly selected local MLX backend, no canned production model response, no tool execution or fallback branch, exact model-file allowlists/hashes in both controller and worker, restricted architecture/custom-code checks, and installed dependency-version checks against the reviewed 34-package manifest. The current manifests match their hard-coded identities, installed version labels match all 34 pins, and the installed MLX-LM generation/loader sources match the preserved tagged sources. This is distinct from claiming that metadata version checks continuously authenticate every installed package byte.

The worker receives canonical structured stdin in a fresh directory/process with isolated Python mode, task-specific caches and offline flags. Existing `HOME` is preserved; `CODEX_HOME` is not repurposed. The model sees explicit request/context text, not an artifact store or repository handle. Initial stages enforce declared request/B/public-check roles and public/authoring visibility; the later checker stage requires declared contract/scenario roles and its narrower visibility rules. These checks do not authenticate source existence, locator/text equality, provenance labels, frozen status or contract/scenario joins. The caller/M2 authoring stage must resolve those against the approved M1/M0 artifacts before calling this write-only provider; granting the provider private store reads is not required.

The fully templated input is counted before weight loading. The inspected pinned `generate_step` defaults to argmax, creates a fresh prompt cache, and bounds emitted tokens; its lookahead work is already disclosed. V2 records exact accepted input IDs and emitted output IDs, uses `selected_model_logprobs`, and sets `behavior_logprobs=None`. No training suitability or learning claim follows. Normal truncation/nonzero-exit/resource-termination paths reject. The runner implements kernel CPU/file-size limits and external wall/output/footprint monitoring, subject to F1. The 3.5 GiB MLX guideline, 4 GiB/20 ms sampled kill threshold and 1 GiB guard are accurately distinguished from a zero-transient memory ceiling. No claim about untrusted M3 worker isolation is established here.

The precommit v2 inventory matches the seven reviewed product/test files; its recorded outputs say 16 focused and 171 full tests passed. Those are preserved owner results, not a fresh full-suite run by this reviewer. The two immutable native smoke receipts have 383 input tokens and 83 emitted tokens under the configured 2048/128 envelope. The second retains distinct sampled and kernel-lifetime memory peaks plus verified normal cleanup. The input-rejection receipt records 381 templated tokens against 16 before loading/inference. Their missing original UTC/source binding, v1 score names, absent input-ID sequence and earlier child-HOME behavior remain explicit limitations; no v1 receipt is promoted to current v2 evidence.

The evidence driver's synthetic context currently uses an unregistered raw text hash as an M0 ArtifactRef and labels an engineering probe `historical_request` (`docs/evidence/M2/run-production-provider-smoke.py:55`, `:68`, `:77`). It is disclosed as a synthetic smoke and supplies no source-admission or historical-grounding evidence. The coordinator has separately identified this and plans to publish the actual source artifact and preserve explicit engineering provenance in its fresh native verification. Do not rewrite the old receipts to invent that missing evidence.

## Remaining gates and reviewer actions

After owner fixes and scoped re-review, the coordinator must bind the reviewed committed source and run the declared current-version native verification with a fresh nonce and actual archived source. Its absence is a downstream verification gate, not an instruction for the owner to exceed the two-call cap. The `larger_unqualified` profile remains explicitly unmeasured on this host and needs its separate bounded qualification before a larger construction batch. Full M2 remains partial until actual M3-backed interface discovery, grounded contract/scenario generation and their reviews; provider tests do not satisfy those gates.

Executed only the two narrow diagnostic scripts below. Both exited 0 and their preserved stderr files are empty. Exit 0 means the scripts reproduced the specified defects and cleaned up their probes, not that the provider passed review.

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review/reproduce-provider-review.py
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review/reproduce-archive-failure.py
```

The first ran two small trusted sleep processes and used synthetic event/backend doubles; the second used only existing test doubles. No new inference, remote compute, download, install, historical/candidate/generated-code execution, or full-suite rerun occurred. Only this report and narrowly scoped reproduction evidence under `docs/evidence/M2/review/` were written. Product files, existing receipts, index, branch and commits were left unchanged.
