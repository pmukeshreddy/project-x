# M2 provider hard-budget preparation

Date: 2026-09-19 UTC. This was source/configuration inspection only. It made no model request, read no credential value, changed no user or machine configuration, and did not change `HOME` or `CODEX_HOME`.

## Disposition

Codex CLI 0.154.0 does not expose a stable, documented per-request input-token or output-token hard-cap option through `codex exec`. The M2 provider can enforce a process wall deadline and local byte limits, and it can validate reported token usage after a successful turn. Post-completion validation is not a hard token cap.

An experimental `rollout_budget` exists, but it is off by default and marked **under development** in the installed CLI. It tracks a weighted combination of prefill and sampled tokens rather than exposing an output-only maximum. The subsequent authorized qualification in `rollout-budget-qualification.md` found that the client emitted a complete response before reporting exhaustion. It therefore does not satisfy the production hard-budget gate.

## Installed 0.154.0 evidence

The installed executable is `/opt/homebrew/Caskroom/codex/0.154.0/bin/codex` (SHA-256 `4f85982624b3898c8991cb80c0981b2aa71070e3537046c9a95950318a95afcc`), reached through `/opt/homebrew/bin/codex`. Its package metadata and `codex --version` both report `0.154.0`.

Commands executed:

```sh
/opt/homebrew/bin/codex --version
/opt/homebrew/bin/codex exec --help
/opt/homebrew/bin/codex debug --help
/opt/homebrew/bin/codex debug prompt-input --help
/opt/homebrew/bin/codex features list
strings "$(realpath /opt/homebrew/bin/codex)" | rg <budget identifiers>
jq '.models[] | select(.slug == "gpt-5.6-luna") | <non-secret fields>' "$HOME/.codex/models_cache.json"
```

The three help surfaces contain no input-token, output-token, `max_output_tokens`, `max_tokens`, or rollout-budget option for `codex exec`. The feature list reports:

```text
rollout_budget  under development  false
token_budget    under development  false
```

The binary contains the installed configuration identifiers `RolloutBudgetConfigToml`, `limit_tokens`, `prefill_token_weight`, `sampling_token_weight`, and `reminder_at_remaining_tokens`; validation strings require a positive `limit_tokens` when enabled. It also contains protocol/status identifiers `codex_rollout_budget_units`, `session_budget_exceeded`, and `budget_limited`. These strings prove that the installed binary contains an experimental mechanism and expected states. They do not prove where a request is interrupted, the maximum overshoot, or that the mechanism is safe for production.

The separate experimental `token_budget` feature is context-window guidance: its installed fields concern reminders and automatic-compaction fallback. It is not a per-call input or output token ceiling. The stable `model_auto_compact_token_limit` triggers history compaction; `model_context_window` describes available context; `tool_output_token_limit` limits tool/function output stored in history. None is a generation-output hard cap, and tools are disabled in the M2 provider profile.

The local non-secret model cache lists `gpt-5.6-luna` with a 272,000-token context window and 95% effective-context percentage, with no `auto_compact_token_limit`. These are capacity metadata, not an exact input allowance, tokenizer identity, model-version attestation, or per-call budget.

## Official documentation and drift

The current [OpenAI configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) describes `features.rollout_budget` as under development and off by default. It documents a positive `limit_tokens`, prefill and sampling weights, and reminders. It does not document this as an output-only limit or specify interruption/overshoot guarantees. The current [configuration schema](https://developers.openai.com/codex/config-schema.json) contains no `max_output_tokens` or `max_tokens` setting. It describes `model_auto_compact_token_limit`, `model_context_window`, and `tool_output_token_limit` with the narrower meanings above.

There is version drift: the current reference names `reminder_interval_tokens`, while the installed 0.154.0 binary requires `reminder_at_remaining_tokens`. M2 must use installed-version evidence and strict parsing, never copy a current-doc field into 0.154.0 or silently repair/fallback.

The [OpenAI non-interactive-mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode) shows usage on the terminal `turn.completed` JSONL event. The actual M2 smoke in `attempt2.events.jsonl` did the same: its only usage object appeared on `turn.completed`. The failed pre-inference attempt emitted `turn.failed` without usage. No inspected help or official page documents an incremental pre-completion usage event. M2 must therefore treat terminal usage as measurement after completion, and must fail when usage is missing or invalid rather than inventing zero.

## Implementation consequence after M1 approval

The stable provider path can truthfully enforce:

- an external monotonic wall deadline that terminates the CLI process;
- a deterministic cap on serialized input bytes before spawn;
- bounded stdout/stderr/final-output retention, with process termination and failure on overflow;
- strict structured-output parsing and forbidden-event rejection;
- terminal usage validation, including failure if reported input/output tokens exceed declared limits; and
- unknown billed USD cost when the subscription-backed CLI reports no price.

The byte and wall controls limit host resources and retained artifacts. They do not guarantee that provider-side token generation stopped at an exact requested count. A successful response whose terminal usage exceeds `ResourceLimits.input_tokens` or `ResourceLimits.output_tokens` must be rejected as a budget violation; that rejection is retrospective.

The previous read-only preparation subtask prohibited model calls; it was a scope limit, not a new user-permission requirement. The already authorized follow-up qualified the exact 0.154.0 strict-config shape under the same actual-home tool-free profile. `rollout_budget` did not add a provider-side output-token field, and the 32-unit attempt emitted the full response before failing. Its precise internal overshoot is unreported. Production hard token enforcement is therefore explicitly unavailable through this provider path. M2 must neither invent a `model_max_tokens` setting nor label post-completion usage checks or the experimental failure as a hard cap.
