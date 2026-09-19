# M2 Codex 0.154.0 rollout-budget qualification

Date: 2026-09-19 UTC. Scope: installed-client request capture plus two predeclared tiny provider calls. No product code, test, contract, repository input, training input, user configuration, credential file, or secret value was read or changed. `HOME` and `CODEX_HOME` were inherited unchanged.

## Result

The experimental `rollout_budget` in Codex CLI 0.154.0 is **not an enforceable hard generation-output cutoff** for M2.

With `limit_tokens=32`, `prefill_token_weight=0.0`, and `sampling_token_weight=1.0`, the CLI emitted the complete 270-byte schema-valid agent message and only afterward emitted `shared rollout token budget exhausted` and `turn.failed`. The failed turn exposed no usage. The predeclared comparison used the same prompt and schema with `limit_tokens=1024`; it emitted the identical message, completed successfully, and reported 189 output tokens plus 21 reasoning-output tokens. The observed response therefore exceeded the 32-unit sampled budget before the client reported failure. The event protocol does not expose the internal `codex_rollout_budget_units`, so the exact unit overshoot and whether reasoning tokens contribute cannot be established from these events.

The hard-cap requirement is unavailable through this installed CLI. A terminal budget failure can reject an over-budget result retrospectively, but it does not stop the provider from generating or the client from emitting the full result. No third call or fallback was made.

## Predeclared bounds and inputs

`rollout-budget-call-plan.json` was written before either provider call. Each call had an external 20-second wall deadline and a 1 MiB combined stdout/stderr cap. Both used explicit `gpt-5.6-luna`, low reasoning, `project_doc_max_bytes=0`, a fresh empty `/private/tmp` staging directory, strict configuration, read-only sandboxing, the complete preflight disabled-feature set, disabled web search, JSONL output, and the strict schema.

The 190-byte prompt has SHA-256 `bb7cd1356d20ef8efa8be7980a562de0b8055a0d3546352651b9acdb2e62cff2`. The 488-byte schema has SHA-256 `62796d8b303ae637c3ee51809cf8cbaa99e7aef3d0ee5fe3b836947a94ce47d0`. It requires exactly one string field containing a fixed 256-character payload. `rollout-budget-real-attempt-1.json` and `rollout-budget-real-attempt-2.json` preserve the event streams, usage or explicit missing usage, process status, timing, and output sizes.

Attempt 1 exited 1 after 5.222201 seconds and retained 607 bytes. Attempt 2 exited 0 after 5.980259 seconds and retained 618 bytes. Neither reached its wall or byte bound, neither produced stderr, and neither emitted a tool event. Attempt 2 usage was 5,978 input tokens, 4,864 cached input tokens, 0 cache-write input tokens, 189 output tokens, and 21 reasoning-output tokens.

## Credential-free request capture

The loopback harness used a custom provider with `requires_openai_auth=false`, never inspected or retained headers, and did not contact the real provider. Its initial iterations established that the CLI first fetches `/v1/models`; the harness also added bounded decoding for either Content-Length or chunked POST framing before the final capture. `rollout-budget-loopback-evidence.json` records all five iterations and their causes; `rollout_budget_loopback_probe.py` is the final harness.

The final `/v1/responses` capture was 29,712 bytes with SHA-256 `ce3aa1421b60e6b6cbd2ac0bb4a4c608327b34caadc4f3821d29842e0c600497`. It selected `gpt-5.6-luna`, supplied zero top-level tools, and contained no top-level `max_output_tokens` or rollout-budget field. The only token-named key was nested inside client-instruction text for an inert disabled tool surface, not a provider generation parameter. For this installed version, the budget is enforced inside the Codex client rather than serialized as a provider-side output limit.

The final capture also staged an `AGENTS.md` containing the unique canary `M2_PROJECT_DOC_ZERO_CANARY_7f6f43d0`. With `project_doc_max_bytes=0`, the request had zero canary hits, zero `AGENTS.md` filename hits, zero project-instruction marker hits, and zero workspace-path hits. This proves suppression of the staged project document and found no incidental global/project instruction marker in the actual-home profile. User-level source files were deliberately not read, so the capture does not establish that a global instruction file existed to be suppressed. Production must keep the same strict setting and drift checks rather than treat this observation as a guarantee for other versions.

## M2 consequence

Codex CLI 0.154.0 can support external wall and byte bounds plus retrospective terminal-usage validation. It cannot meet an exact hard input/output-token enforcement requirement through a documented request setting or the qualified experimental rollout budget. Under the existing hard-cap requirement, this provider path is blocked.
