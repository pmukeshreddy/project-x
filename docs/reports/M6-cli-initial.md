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

This initial checkpoint preceded the authoring/history and final command joins
described below. It did not grant admission to the provisional Click fixture.

## Complete command integration

The final command layer now composes the actual Factory author/import/grade,
M7 native run/training and M8 evaluation/audit services. Native configuration is
inert; the service's selected job owns startup, child work and shutdown accounting.
Run case seeds, invocations, training checkpoints/demonstrations and evaluation
configurations pass through unchanged. Terminal cleanup preserves both original
and cleanup errors. No execution callback or replacement native service is added.
The full JSON configuration, request shapes and executable module commands are in
`docs/runbook.md` and `docs/interfaces-M6.md`; Linux prerequisites and exact native
launch/recovery parameters remain in `docs/reports/M7-native-launch.md`.

The actual M4 grade wrapper freezes opaque input dependencies in its new consumer
configuration, claims before execution and reconciles the original costs. It
selects one immutable result per invocation. Frozen parent, M4 pending, and early
original-result publication outages retain exact bytes/results and recover without
another grade. The coordinator's confirmed early-original publication loss was
reproduced before the fix and now has both library and CLI regression coverage.
An unfrozen unknown attempt refuses redispatch; missing cost channels remain unknown.

Final affected CPU checks: **33 passed in 23.50 seconds**, exit 0. These cover actual
pre-execution M4/Registry grading, publication recovery/cost binding/quarantine,
M5/lifecycle recovery, actual inert native service composition, thin delivery of
all new command inputs, private pending capability readback and cleanup error
preservation. Diagnostic worker/native/service-entry substitutions are confined to
tests and provide no model, human, training or study evidence. Exact observed HEAD,
before/after source hashes and command are in
`docs/evidence/M6/cli-initial/final-receipt.json`; stdout is `final-focused.txt`.
All captured source hashes were unchanged during these checks. M8 is a concurrent
owner; later M8 edits are separately reviewed and are not attributed to this run.

No model generation, native initialization, GPU, Docker or human approval was
executed for this command slice. Existing 13-grade Click TEST and runner Docker
receipts are reused. The coordinator owns the one final stable CPU suite and
cached actual CLI baseline grade. Real Click source scope remains provisional
and its retained contract stage exhausted; no released real task, GPU update or
held-out experimental result is claimed.
