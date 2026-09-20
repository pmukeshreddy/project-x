# M8 audit and statistics round-1 checkpoint

This checkpoint addresses the six findings in `docs/reviews/M8-independent-core-round1.md`. It is CPU unit-diagnostic evidence, not an audit result, human approval, task admission or experiment.

Implemented corrections:

1. Patch error estimates now use the specified denominators: verifier-accepted, human-invalid and human-valid respectively. Equal- and unequal-probability hand calculations are covered.
2. Rejected-source audits are distinct units keyed to an actual completed M6 `m6-source-admission` construct job, exact `CandidateRecord` and exact `m6-source-disposition`; they do not require or fabricate a rollout.
3. A typed population frame and seeded stratified plan derive all selections and inclusion probabilities. The service validates every frame unit, requires the complete patch selection, and retains missing adjudications as unresolved.
4. Patch validity, environment validity and checker assessment are separate. Infrastructure-only outcomes cannot establish checker defects. Source defects bind and quarantine the source version, not a verifier.
5. Best-of-k is aggregated once per task/policy-seed over the complete frozen episode set, with distinct metric names and denominators.
6. Training variability remains explicitly unavailable until independently trained replicate checkpoints exist; inference policy seeds never remove that limitation.

Opaque frame, plan, selection, attestation, configuration, report and consumed leaf dependencies are registered in the actual Registry. `AuditService.audit` creates one selected Registry operation with a cost snapshot. No second audit ledger exists.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_evaluation_core.py tests/test_evaluation_statistics.py tests/test_audit_statistics.py tests/test_audit_selection.py tests/test_audit_service.py -q
```

Result before commit: `21 passed in 0.78s`.

No human enrollment or signatures were created. The service diagnostics cover missing-attestation accounting and exact external trust rejection rules; they do not assert a human-reviewed sample. No broad suite, Docker run, model call, corpus acquisition, GPU run or experiment was performed.
