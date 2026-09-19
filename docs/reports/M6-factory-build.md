# M6 complete BUILT root and frozen solver package

Status: scoped implementation complete; independent review pending. Full M6
orchestration, lifecycle admission and CLI remain open. No real Click task was
built: its three-call generation budget ended without a contract/scenario. These
results establish the implemented packaging and failure paths, not feature validity.

## Usable boundary

The [interface](../interfaces-M6.md) publishes `BuildInputs` and
`TaskBuilder.build`, `recover`, `retry_publication` and `solver_package`.
M5 calls `solver_package(T0) -> bytes` on the builder configured with T0's exact
construction revision. It gets the already-frozen archive after complete
component/inventory/join/policy/quarantine validation. It does not receive a
replacement package or qualification result.

The builder resolves actual M0 CandidateRecord, SourcePair, RequirementContract,
ScenarioPlan, VerifierBundle and EnvironmentRecipe values and the reviewed M3
PreparedEnvironment/SandboxPolicy/source archive types. It validates exact joins,
candidate screening/license/partition, requirement IDs, submission rules and pinned
recipe/policy/dependency bytes. It uses no M2 or M4 service helper. It does not
duplicate M5's H projection: T0.reference_solution remains the exact raw H ref.

The public tar is newly assembled from a complete explicit B file allowlist,
selected disclosed contract fields, declared PUBLIC checks and approved runtime
fields. Its inventory binds all actual paths, bytes, sizes and executable flags,
the archive's raw SHA-256 and exact public component refs. All components and the
complete package freeze before the immutable BUILT T0. Private manifests, source
joins, adapter/oracle payloads and provenance are not copied into that projection.
No source/checker/build hook executes or is extracted on the controller.

## Persistence and failure behavior

The private serialized request is the registry job input so absent requested
artifacts can still receive an attributable failed construction operation. An
intent cost snapshot is committed before assembly. Concurrent same-claim intents
conflict rather than independently authorizing assembly. Missing prerequisites
produce typed failed OperationResults and accounting, without a BUILT root.

The frozen receipt contains exact SolverView/package refs, dependency identities,
UTC time and construction/storage cost snapshot. It is reconciled through the
reviewed registry before root publication. Recovery after that point reuses the
receipt; duplicate completion does not create another task. Bounded pending receipt
bytes carry a hash and claim for publication retry. Before-freeze process loss
retains an unknown attempt and requires explicit supervisor reconciliation or
abandonment/retry; it is never silently reassembled. An abandoned attempt cannot
finish a new frozen outcome. Changes to the contract/package require a new root.

Construction wall time through the freeze is partial measurement. Receipt/root
publication, registry commits and recovery overhead remain explicitly unknown in
a separate storage cost, not zero. Prior interrupted attempts and their unknown
costs remain in the registry. This slice does not claim cross-service atomicity,
power-loss durability, complete upstream authoring-cost aggregation or a global
stage budget. Later M6 orchestration owns those service joins and budgets.

## Actual verification

Initial [red output](../evidence/M6/factory-build/red.txt) records **14 failures**
on the absent production API, exit 1; [red receipt](../evidence/M6/factory-build/red.json)
records the command/times. The [first implementation output](../evidence/M6/factory-build/first.txt)
records **14 passed**, exit 0. Added checks cover changed-root/H lineage, source
links, hash-protected pending bytes, and actual child-process crashes on either
side of the freeze boundary.

Final command:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory.py tests/test_registry.py tests/test_registry_durability.py
```

**54 passed, exit 0**, pytest 13.98 seconds; observer elapsed 14.1079592 seconds.
Execution interval: 2026-09-19 22:15:50.559930–22:16:04.671396 UTC.
The [raw output](../evidence/M6/factory-build/focused.txt) and
[verification receipt](../evidence/M6/factory-build/verification.json) record the
exact command, environment, observed HEAD `45f934e295051ff8f23a8644f6f738f4b6fa659d`
and 23 source/test SHA-256 pairs. All captured hashes matched before/after the run.
M6 implementation files were working-tree additions at execution; the observed
HEAD is not their content identity or a claim about unrelated concurrent paths.

Tests use complete resolvable diagnostic M0 artifacts in separate pytest temporary
state. Test-only wheel pin substitutions use inert diagnostic bytes; they cannot
establish real M3 compatibility, sandbox qualification or a successful feature.
Real child processes exited at injected points; pre-freeze recovery preserved
unknown cost and required explicit retry, while post-freeze recovery preserved
the selected bytes and cost snapshot. Existing registry filesystem/crash checks
also passed. No full suite, Docker, network, H execution or model invocation ran.

## Remaining gates and limitations

M5 must perform genuine baseline/reference/control/reset execution, accepted-report
verification and exact T0/evidence-package/policy human attestation. Packaging
validates public field selection and byte lineage; it does not authenticate the
semantics of caller-declared public instructions/tests or independently prove
source history, license truth, image contents or active mounts. Reviewed M1/M2/M3
and qualification gates retain those responsibilities. The supported runtime is
the pinned M3 offline Click profile; unsupported recipes fail explicitly.

The source path policy is deliberately conservative and the complete B allowlist
must be supplied. Limits are finite and shared registry caps can reject large
closures; no automatic cap increase, pruning or repair was added. Missing/corrupt
state remains a typed error. Existing tests observe process crashes on macOS 26.3
arm64, CPython 3.13.7 and SQLite 3.50.4, not physical power loss. All shared M0/M3,
registry and configuration files were left untouched by this slice.
