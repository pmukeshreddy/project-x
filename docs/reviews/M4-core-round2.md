# M4 core independent review — round 2

Reviewed same-owner fix `430df6f4a2406fd9a669e11de2e963a2dcf73be8` against core `02aee5494ba93b195e0b38f9fbc367f06585df5a`, restricted to the two findings in [round 1](M4-core-round1.md) and their directly affected code, tests, documentation and new evidence.

**Specification verdict: PASS for the mechanical grading-core checkpoint.** Both confirmed findings are closed. M2-dependent checker proposal/finalization remains a separate slice; this verdict does not confer task qualification or admission.

**Implementation quality verdict: PASS for the reviewed fixes and resulting core checkpoint.** No residual P0, P1 or P2 finding in this scoped re-review.

## Finding closure

- **P1, resource envelope — closed.** `src/feature_rl/verifiers/loader.py:83` compares the fixed recipe's wall, CPU, memory, PID, disk and output allowances with the contract and raises `ValueError` if any exceeds the grant. The existing grading handler converts that rejection to `unsupported_semantics` with null reward before source/workspace creation. M3's reviewed recipe-to-policy checks continue to bind the enforced envelope. Equal/larger contract grants remain supported; solver token/tool budgets are excluded from worker comparisons. New per-field regressions and the unchanged reviewer reproduction cover the rejected and accepted boundaries.

- **P2, trusted baseline attribution — closed.** `src/feature_rl/submission/service.py:38` resolves trusted B once before candidate parsing and converts baseline `SourceRejected`/`ArtifactSizeLimitError` into `ArtifactIntegrityError`. That error bypasses grading's candidate-zero catch and reaches its infrastructure/null path. Candidate delta parsing still uses `SourceRejected`. Focused cases cover malformed B, serialized size overflow, expanded size overflow and malformed candidate delta; they preserve the submission/seed and complete unrun case ledger. The unchanged reviewer reproduction now records infrastructure failure, null reward and no build evidence.

## Evidence and execution limits

Inspected [the fix receipt](../evidence/M4/core-round1-fixes/receipt.json), changed source/tests and focused output. Fresh hash checks matched all four receipt-bound source files, including the unchanged reviewer reproductions, and all three new log hashes/byte counts. The reviewed commit contains the bound product/test bytes.

Recorded owner evidence: the new regressions first yielded **9 failures and 2 passing controls**; the isolated resource fix yielded **7 passes**; the final focused command yielded **58 passes in 0.53 seconds**, exit 0, including both original reviewer reproductions and the candidate-zero control. [Final focused output](../evidence/M4/core-round1-fixes/focused-green.log).

No additional test suite, native/Docker execution, model/tokenizer call, H/history/private-authoring read, network/download or child agent was used in this re-review. Product files, index, branch and commits remained read-only. These are preflight diagnostic corrections; the prior real Click route evidence retains its original scope. Genuine feature qualification, human acceptance and GPU/training/evaluation execution remain separately unverified milestones, not requirements silently satisfied by this PASS.

The reviewed mechanical core is ready for M5 and coordinator integration under its documented admission boundaries.
