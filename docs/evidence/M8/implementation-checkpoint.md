# M8 independent implementation checkpoint

Recorded 2026-09-19 on a shared live worktree. This is unit diagnostic evidence, not an experiment or human approval.

Implemented owned paths:

- frozen roster, relation closure, arm/budget/protocol/preregistration validation;
- adaptation funnel with complete loss accounting;
- all-assigned and valid-only rates plus task/family paired statistics and seeded family-clustered uncertainty;
- frozen audit selection, inverse-probability weighted error estimates, exact Registry rollout/M4 receipt lookup, canonical M5 SSHSIG human adjudication, quarantine and descendant tracing.

Focused command:

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/test_evaluation_core.py tests/test_evaluation_statistics.py tests/test_audit_statistics.py -q
```

Result before commit: `13 passed in 0.15s`.

The broader live-tree diagnostic completed as `625 passed, 7 skipped in 304.39s`; it overlapped concurrent upstream work and is not evidence for a frozen integration revision. The seven skips were optional Torch/SkyRL tests because Torch is not installed. The focused 13 owned tests are the checkpoint evidence.

CPU-only checks cover hand-derived weighted audit estimates, null empty denominators, exclusion of targeted samples, task versus family weighting, deterministic bootstrap results, complete assignment accounting, lineage leakage, paired seeds, matched budgets and adaptation-stage conservation. No Docker execution was needed for this pure controller/statistics slice. Existing M4 Docker evidence belongs to M4 and is not re-attributed here.

Exact remaining gate: M7 must publish the agreed real runner method accepting an explicit nonnegative `case_seed` and returning a `RolloutRecord` whose sole seed and M4 receipt match it. M8 will then implement `EvaluationService.evaluate(config)` with one runner call per frozen assignment and no duplicate grading. Real evaluation also requires reviewed upstream admission, frozen withheld tasks, actual arm checkpoints and policies, and available execution resources. Actual audit output requires an externally administered M5 enrollment and signed human adjudications.
