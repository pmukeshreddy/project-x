# Initial actual CLI and service composition

`python -m feature_rl` now exposes strict JSON `screen-source`, `construct`,
`qualify`, `accept`, `release` and current-admission `resolve` commands.
`feature_rl.cli:main` is the usable console-entry callable; shared package metadata
is unchanged. The source/complete-artifact path opens no Docker or model. Runtime
commands compose the actual M3 boundary-qualified runtime, actual M4 grader,
actual M5 service and reviewed M6 lifecycle/resolver from explicit service pins.

`config-schema` and help work without service state. Unknown fields, duplicate
JSON keys, nonfinite values and unsafe/oversized input files reject before
composition. The CLI uses M0 request schemas rather than shadowing them. Selected
results retain their dispositions; failures return nonzero typed operation JSON.
Concrete pending publication capabilities retain exact private bytes, claims,
costs and timestamps on stderr. This is an output receipt, not an object loader.

The ten focused CLI checks passed in 1.35 seconds. They cover real source Registry
selection/replay, missing actual build prerequisites, input denial, absence of
runtime configuration and lost-publication receipt replay with exact frozen bytes.
The actual module help command also exited 0. No worker, native backend, model or
human verification was invoked. Receipt/source hashes captured after this check
are in `docs/evidence/M6/cli-initial/receipt.json`.

Remaining actual authoring/global history and M7/M8 command joins are still open.
These commands do not claim a completed experimental pipeline or grant admission
to the provisional Click fixture. The setup and all current command formats are
in `docs/runbook.md` and `docs/interfaces-M6.md`.
