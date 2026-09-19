# M2 authoring re-review — round 3

**Specification compliance: FAIL. Implementation quality: FAIL.** The original B1–B3 examples are fixed, but two P2 compatibility defects remain in the newly introduced recovery validator. No P0/P1 finding in this round. This is the complete scoped batch for the same M2 owner.

Reviewed only product `d64f172efb2d50ce9303725a08ed2fcfd9f4a65f` and documentation `d973d19801881b7eb1222973398abea1da4034eb` against `f0102723cb2d18543dc2c57c01ea116c3d141bbd`: B1–B3 recovery/public-check fixes, their tests/evidence, and the `m4-sha256-v1` reproduction default. Prior binding requirements and review scope remain as recorded in [round 2](M2-authoring-round2.md).

## Confirmed closures

- **B1:** both services now share bounded archive resolution and checks for the exact request, output schema, contexts, attempt/status, content, usage, and cost. A fixture using the actual provider controller and `replay_result` resumes successfully with the original inputs; changing the instruction or cost rejects before provider reuse.
- **B2, post-execution case:** pending provider publication propagates regardless of generation success. An actual malformed-response fixture can finish `replay_error`, enter `recovered_error=`, and publish one rejected journal without another provider call. The new rejection-journal replay path remains available.
- **B3:** the scenario source set now includes exactly `contract.public_checks`, preserving rejection of unrelated refs. The added positive test exercises an explicitly frozen public check.
- The retained reproduction now uses **`m4-sha256-v1`**, matching the implemented M4 loader/materializer. Documentation correctly distinguishes structural ID coverage from semantic qualification.

## Remaining findings

### C1 — P2: recovered pre-execution failures cannot finish authoring journaling

Location: `src/feature_rl/requirements/service.py:89`–`:98` (`validate_recovered_generation`), also called by `ScenarioAuthoringService.generate`.

The shared validator requires the same 11 archive names for every recovered outcome. The existing provider deliberately publishes smaller bounded records before execution: registration recovery has `{attempt, status}`, and an oversized-input preflight rejection has `{attempt, preflight, cost, status}`. These are complete records for their respective paths; no model/backend execution took place.

The diagnostic exercises both real provider-controller paths with a one-time publication fault. `replay_error` succeeds and returns `publication_complete=true`, but passing that error to the authoring service raises `ValueError: recovered generation archive set is incomplete` before a rejected journal is written. Both cases have zero backend verifications, runner invocations, and recovery provider calls. The caller retains evidence, but cannot complete the advertised replay-and-journal operation through the service.

Validate the provider's actual bounded pre-execution record variants and their request/schema hashes, status, disposition, and available costs. Do not manufacture missing response/event archives or repeat generation to fit the post-execution shape.

Evidence: `registration_replay` and `preflight_replay` in [diagnostics.json](../evidence/M2/review-authoring-round3/diagnostics.json).

### C2 — P2: the response-artifact read cap is smaller than a valid encoded provider receipt

Location: `src/feature_rl/requirements/service.py:112`–`:114`, also used by scenario recovery.

Every decoded artifact payload is capped at 1 MiB. However, the provider's `generation-response` JSON contains base64-encoded stdout/stderr: a raw retained stream within the declared 1 MiB output boundary can produce a response-artifact payload larger than 1 MiB.

The inert diagnostic uses 1,650 ordinary token events, below the 4,096-token limit, and a 946,130-byte raw event stream before the runner fills two identity hashes. The provider accepts the successful fixture and completes all publication recovery. Its response receipt is 1,262,069 bytes. Authoring resume then raises `ArtifactSizeLimitError` at the 1,048,576-byte payload cap, with no provider reuse. Thus the new recovery path rejects a valid result that fits the existing provider boundary.

Use bounded per-artifact read limits that account for the response receipt's encoding and metadata, including the outer CAS envelope. This is a controller receipt-size correction; native/token/memory execution bounds should remain unchanged.

Evidence: `response_receipt_size`.

## Evidence and execution status

Read the owner's retained correction receipt and logs: **146 focused tests passed in 1.01s, exit 0**. The preceding wrapper failure is explicitly retained and not treated as a captured pytest exit. No broad suite was rerun.

One new, source-pinned diagnostic ran:

```sh
PYTHONPATH=src .venv/bin/python -B docs/evidence/M2/review-authoring-round3/reproduce.py
```

**Exit 0, 0.480583 seconds, empty stderr.** [reproduce.py](../evidence/M2/review-authoring-round3/reproduce.py) reuses the prior fixture setup with the exact reviewed product target; the result records its source hashes and reused-bootstrap hash. It checks the changed recovery boundary using synthetic CAS stores, inert backend verification, and fabricated runner observations. These are controller diagnostics, not native model or feature-construction evidence.

No native generation, tokenizer, Docker, network, production/private-store, H/history, executable application, broad audit, product/index/commit change, or subagent was used. Cross-module candidate-budget enforcement remains M6 responsibility.

Actual construction is unchanged: three model calls exhausted the contract stage, ending with strict duplicate-key rejection; no grounded frozen contract or scenario exists, and no H feasibility review is authorized. This execution failure is separate from code compliance and does not justify a handwritten replacement. Fix C1/C2 through the existing owner, then review that small compatibility delta before root's integration checkpoint.
