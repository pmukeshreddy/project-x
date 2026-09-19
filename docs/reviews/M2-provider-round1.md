# M2 provider fix-round 1 re-review

Reviewed product commit `7239379ebf6921fd816b1bded9bb07a685b84c1a` against previously reviewed `8a4637552b87dbf5e7bfe57b0a843c3a59327dad`. Read the complete supplied review package, prior F1–F5 report, all current generation product files, updated tests/interfaces/report and v3 verification receipt. Coordinator documentation and the prepared native driver are integration context; this verdict covers the provider slice. Reviewer: `/root/review_m2_provider`, 2026-09-19.

**Specification compliance: FAIL pending the two P2 corrections below.** The original concrete failures have been corrected, but exact event types remain insufficiently enforced and a new resource-rejection path bypasses call recording.

**Implementation quality: CHANGES REQUIRED.** The cleanup, JSON transport, event identities and publication recovery are substantially improved. No remaining P0/P1 defect was identified in the scoped correction review. Current-version native execution remains a separate coordinator gate after review clearance.

## Remaining findings

### R1 — P2: Boolean/integer Literals still accept the wrong JSON types, weakening prior freshness checks

Locations: `src/feature_rl/generation/protocol.py:15`, `:35`, `:36`, `:68`, `:69`, `:99`, `:100`; decoding at `src/feature_rl/generation/provider.py:236` and subsequent identity checks at `:303` and `:330`.

Pydantic's Literal validation converts equal numeric values into the configured literal even with inherited strict mode. The later `is True`/`is False` checks therefore inspect already-coerced booleans. Five separate full-provider diagnostics accepted `local_files_only=1`, `remote_code=0`, `protocol_version=3.0`, load-event `fresh_process=1`, and completion-event `fresh_prompt_cache=1`. Each returned `record.success=true` and `accepted_response_usage=true`. The earlier raw `is True` freshness checks rejected numeric 1, so this is also a regression in those checks.

The current model/manifest hashes and contradictory boolean values are checked correctly; this finding is specifically the advertised exact-type boundary. These are synthetic mutated event streams, not claims about events emitted by the real worker. Enforce the original JSON type before Literal coercion, locally in the provider protocol, and cover every boolean/integer Literal event field with numeric-equivalence negative cases. Preserve the strict success values, schemas and identity comparisons. No M0 or dependency change is required for this correction.

Reproduction: `remaining_F3_literal_coercion` in [round1-diagnostics.json](../evidence/M2/review-round1/round1-diagnostics.json), produced by [reproduce-round1.py](../evidence/M2/review-round1/reproduce-round1.py).

### R2 — P2: New byte-cap preflight rejects a validated request without an attributable failure record

Locations: `src/feature_rl/generation/provider.py:492`–`:500`, before attempt creation/publication at `:501` and `:538`.

The new request/schema/prompt size check raises a bare `ValueError` outside the provider's failure-record paths. A request that passes `GenerationRequest` validation but exceeds its declared serialized-input budget now produces no `GenerationProviderError`, attempt, status, cost or recovery information. The diagnostic supplies an 8,382-byte validated request with a 4,096-byte stdin cap: the runner is correctly never called, but the available archive callback is also never called and the exception has no `record`. This regresses the previous serialized-worker byte-cap path, which was inside the recorded failure handler.

Refusing oversized input before inference is correct. Route this resource rejection through a bounded typed failure/attempt path rather than bypassing the public error and accounting contract. Preserve the identifiers, declared/observed sizes, request/schema identities and failure cause without retaining an oversized payload or executing the worker. Add a regression that checks both no execution and an attributable rejected-call record.

Reproduction: `new_unarchived_preflight_rejection` in [round1-diagnostics.json](../evidence/M2/review-round1/round1-diagnostics.json). The existing prepared native driver catches `GenerationProviderError`; this untyped branch also illustrates why callers cannot rely on that documented failure contract. The driver itself was not executed or approved as part of this review.

## Original finding disposition

| Finding | Re-review result |
|---|---|
| F1: monitor exception cleanup and lost observations | **Original reproductions closed.** The runner now enters `finally` cleanup after monitor errors/interruption, returns partial measurements, and fails on the first missing observation. Independently repeated the original exception and missing-observation probes with trusted sleepers: both returned `monitoring_failure`, one recorded failure, exit -9, and verified group absence. See [runner-closure.json](../evidence/M2/review-round1/runner-closure.json). |
| F2: strict JSON transport | **Closed.** `model_validate_json(canonical_json(content))` accepts the original tuple/enum JSON plus a genuine M0 `UTCDateTime` field. Duplicate/envelope checks remain; strict model validation is preserved. |
| F3: incomplete/contradictory events | **Original examples closed; exact-type requirement remains open as R1.** Independently confirmed missing/extra fields, wrong config/tokenizer/weight hashes, wrong dependency versions, remote/nonlocal flags, changed completion ID and positive model log probability now reject. Complete typed event models, repeated request identity, prompt/worker hashes and memory/timing checks are present. |
| F4: archive publication failure | **Original reproduction closed; new preflight gap is R2.** An injected response-publication failure now retains the partial record, response, token observations and known costs. Replay resolves all eleven archives and returns successful publication with exactly one synthetic runner call. Registration failure prevents execution; repeated publication failure retains another typed recovery object. |
| F5: failed-envelope usage | **Closed.** Unknown-requirement output fails while `accepted_response_usage=false`, `accepted=null` and known token observations remain available. Costs retain the known counts. |

The independent protocol/recovery diagnostic resolves every recovered M0 reference through the actual ArtifactStore. It does not treat stored data or test doubles as native execution evidence. The source review also checked the replay loop, status publication, underlying generation-failure preservation and pre-execution registration path; no additional finding was raised in those changed paths.

The selected backend/model/architecture and model-file verification remain unchanged in scope. Worker source and prompt hashes now bind the event stream, source-file and configured-backend identities are in UTC attempt metadata, and the worker still receives explicit structured stdin in a fresh isolated/offline process with task caches and preserved HOME. Stage/visibility controls and caller-side source/provenance/frozen-join responsibilities remain as previously reviewed. Sampled memory limits and their guard-band limitation remain explicit. Model scores remain `selected_model_logprobs` with `behavior_logprobs=None`; no training claim or backend fallback was introduced.

## Evidence and remaining gate

The owner's receipt records **46 focused / 201 full tests passed**, with zero native calls. Its eight-file product/test inventory and raw-output hashes were checked against the reviewed files and preserved output. These are the owner's retained runs; no full suite was rerun by this reviewer.

The reviewer ran only:

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round1/reproduce-round1.py
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round1/check-runner-closure.py
```

Both exited 0; both stderr files are empty. The first uses scoped backend/runner doubles and the real store to reproduce R1/R2 and check the original protocol/recovery cases. The second executes only two short trusted sleep probes for the original F1 cleanup check. Exit 0 records successful diagnostic assertions, not provider approval. Exact commands, source hashes, output hashes and observed exit statuses are retained in `docs/evidence/M2/review-round1/review-verification.json`.

No MLX/native inference, download, install, remote compute, historical/candidate code execution or whole-suite rerun occurred. Only this report and new evidence under `docs/evidence/M2/review-round1/` were written; original review/evidence, product, index, branch and commits were preserved. The same M2 owner should fix R1/R2, followed by scoped re-review. The coordinator must then execute its declared source-bound current-protocol native check. V1 receipts remain unchanged and do not prove v3; larger envelopes remain unqualified, and full M2 remains partial until actual runtime-backed authoring/scenarios are delivered.
