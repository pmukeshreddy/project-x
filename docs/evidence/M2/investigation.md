# M2 generation-provider feasibility investigation

Date: 2026-09-19 UTC. Scope: provider protocol evidence only. No feature requirements, Click contract, checker, product code, tests, training, or candidate execution was produced.

## Result

The existing ChatGPT-authenticated Codex CLI completed one tiny structured-output inference after one diagnosed schema repair. The successful command exited 0 in 3.118 seconds, emitted schema-valid `{"ok":true,"nonce":"M2_PROVIDER_SMOKE_V1"}`, and reported actual token usage. Its complete JSONL trace contains only `thread.started`, `turn.started`, one `agent_message`, and `turn.completed`. It contains no tool, command, MCP, app, web-search, browser/computer-use, subagent, question, or file-change event.

This establishes feasibility of the restricted CLI protocol under the current subscription when combined with the corrected preflight's loopback request-body capture. The remote inference trace alone does not reveal the submitted tool-definition array or echo the effective configuration, so it is not independent proof of those request fields. It also does not establish feature-generation quality, training suitability, a backend weights/version identity, or a priced per-call cost.

## Inputs and selection

- CLI: `/opt/homebrew/bin/codex`, version `codex-cli 0.154.0`.
- Authentication boundary: actual `HOME` and `CODEX_HOME` were unchanged. `--ignore-user-config` excluded user configuration while preserving the existing authenticated session. No auth file or credential value was read, copied, staged, or printed.
- Model: explicit `gpt-5.6-luna`, selected from the actual-home non-secret model cache fetched at `2026-09-19T08:00:55.577408Z`. The cache lists it as visible, API-supported, and supporting `low` reasoning. The complete retained selection facts are in `model-selection.json`.
- Prompt: exact 226-byte `smoke-input.txt`, SHA-256 `3a71dc8f5e71b6bdb6bb38b986d478d5e72fd7e22f6ba2720f3c03b0c6a214df`.
- Final schema: exact 282-byte `smoke-output.schema.json`, SHA-256 `cfe5365f37e41bb6c64960513364b207272ddecf5b5e867fc4729fb0cfb7d651`.
- The probe was protocol evidence only. It received no B, H, diff, private checks, repository files, or authoring conversation.

Each attempt used a newly created directory under `/private/tmp`. Its ancestors `/`, `/private`, and `/private/tmp` had no `AGENTS.md`. The staged allowlist contained only `smoke-input.txt` and `smoke-output.schema.json`. The repository was not staged. The two staging directories are recorded in `status.json`; they were deliberately preserved after the execution wrapper rejected a pre-call cleanup command, and contain no secrets.

## Exact execution profile

`command.txt` records the normalized argv. Both provider requests used the same profile and differed only in schema content and fresh staging path:

- 120-second process deadline enforced by `/usr/bin/perl` `alarm` before `exec`;
- `--ephemeral`, `--sandbox read-only`, `--ignore-user-config`, `--ignore-rules`, `--strict-config`, and `--skip-git-repo-check`;
- all preflight-listed tool features disabled, including `shell_tool`, `unified_exec`, `apps`, `hooks`, `multi_agent`, browser/computer surfaces, plugins, skill surfaces, goals, memories, sleep, image viewing, and both request-user-input controls;
- `web_search="disabled"`;
- explicit `--model gpt-5.6-luna` and `model_reasoning_effort="low"`;
- JSONL events and the supplied output schema.

The command runner retained 500 tokens of attempt-1 wrapper/event output and 392 tokens of attempt-2 wrapper/event output, both far below its 8,192-token (~32 KiB) retention ceiling. The retained final-output file is 43 bytes including its newline. Neither trace was truncated.

## Attempt 1 and diagnosed repair

Attempt 1 exited 1 after 2.216 seconds. The API returned HTTP 400 `invalid_json_schema`: `In context=('properties', 'ok'), schema must have a 'type' key.` No inference completed and no usage event was emitted. The exact failed schema is retained as `attempt1-invalid-output.schema.json`; the complete trace is `attempt1.events.jsonl`.

The one repair added `"type":"boolean"` to `ok` and `"type":"string"` to `nonce`. No prompt, model, option, authority, or feature flag changed. Per the probe limit, no further repair or provider attempt was made after attempt 2.

## Attempt 2 evidence and acceptance checks

Attempt 2 exited 0 after 3.118 seconds. `attempt2.events.jsonl` is the complete event stream and `attempt2-output.json` is the extracted final object. The output parses as JSON, has exactly the required keys, and matches both constants in the strict schema. No forbidden event type or nested item type occurs.

Reported usage is 5,821 input tokens, 0 cached input tokens, 0 cache-write input tokens, 25 output tokens, and 0 reasoning-output tokens. Usage is therefore known and retained. The CLI did not report a billed USD amount, so cost remains explicitly unknown rather than zero.

The CLI event protocol does not echo the selected model or backend model version in this trace. The configured model identifier is proven by the exact argv and was accepted by the provider; no mismatch signal was observed. A future implementation must fail if a provider event or response reports an identity different from `gpt-5.6-luna`. It must not invent a backend version when none is reported.

The successful remote trace's absence of tool events proves that no tool executed. It does not by itself prove that zero tool definitions were sent. That stronger request-construction fact comes from `docs/evidence/preflight.md`, whose corrected actual-home loopback capture used the same strict configuration and recorded `tool_count: 0`. Production integration should retain request-construction checks and treat protocol/configuration drift as a failure rather than inferring isolation from a quiet trace.

## Handoff gate

Provider implementation and the Click contract remain intentionally deferred until M1 review handoff. This probe supports an implementation using this exact restricted profile, complete event auditing, strict schema validation, deadline/output bounds, and explicit unknown billed cost. The production provider must preserve request/response archives and reject malformed output, forbidden events, missing/invalid usage, truncation, nonzero exit, and any reported model mismatch.

The later bounded budget qualification is recorded in `rollout-budget-qualification.md`. It found that Codex CLI 0.154.0's experimental `rollout_budget` emits a complete over-budget response before its failure event and sends no provider-side output-token limit. It cannot supply the requested hard token cutoff; only wall/byte bounds and retrospective rejection are supported by the observed path.

After that failure, the project explicitly selected local MLX with pinned `mlx-community/Qwen3-4B-Instruct-2507-4bit` as the candidate authoring backend. This disclosed selection is qualified separately in `local-mlx-investigation.md`; it does not replace or weaken the retained Codex CLI result.
