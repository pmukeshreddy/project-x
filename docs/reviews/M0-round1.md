# M0 fix round 1 independent re-review

Scope: base `313a463` through head `cb53f99`, including initial fix `583d963` and its supplemental stop/reward correction. Reviewed the original `docs/reviews/M0.md`, appended owner report, complete supplied diff and focused code diff, changed production validators, regression tests, interfaces and recorded evidence. This is a scoped re-review of the three findings and new breakage in their fix diff, not a new whole-project review.

**Specification compliance: PASS for this fix scope.** All three original findings are **ADDRESSED**, and the final ordinary-stop behavior agrees with specification §11.

**Implementation quality: PASS for this fix scope.** No new substantive defect found in the final changed code. The fixes use local cross-field validation, preserve honest unknown/invalid records, and have targeted negative and positive regression coverage.

## Finding disposition

| Original finding | Status | Review evidence |
| --- | --- | --- |
| P1 — Author-visible reference solutions | **ADDRESSED** | `src/feature_rl/contracts/models.py:348` now permits only private/evaluation references in SourcePair. The same allowlist applies to TaskBundle reference_solution and private_oracle at line 575. Tests reject all other visibility classes and exercise the actual store consequence: authoring bytes are readable by an author, cannot be registered as H through either typed manifest, and accepted private/evaluation H bytes are denied to that role. |
| P1 — Invalid trajectory accepted for training | **ADDRESSED** | `src/feature_rl/contracts/models.py:672` maps invalid_trajectory to invalid_measurement and infrastructure_failure to infrastructure_failure, rejecting contradictory dispositions and training eligibility. Reward restrictions keep these records unmeasured. Positive tests retain saved submissions for separate grading. Existing policy-identity validation still runs and its test now targets the intended mismatch explicitly. |
| P2 — Evaluation success without measurement | **ADDRESSED** | `src/feature_rl/contracts/models.py:732` requires measured trial outcomes to include a typed rollout reference, rejects rejected-but-resolved trials, and requires null outcomes for unmeasured/invalid dispositions. At line 788 a successful report requires both a measured trial and a positive-sample, nonnull metric. Tests reject each missing-measurement combination and preserve blocked trials alongside measured trials and wholly unknown provisional reports. |

## Stop/reward regression review

The intermediate `583d963` rule forced zero reward for malformed-action and token/tool/time-limit stops. That behavior is superseded in `cb53f99`; it is not approved by this review.

The final validator at `src/feature_rl/contracts/models.py:682` constrains only `candidate_failure`, now expressly documented as a terminal candidate build/import/worker grading failure. Ordinary malformed-action and limit stops can retain measured reward **0 or 1**. `test_review_ordinary_stops_preserve_final_submission_grade` covers both rewards across all four stop reasons, retains the submission and training eligibility, and rejects absent grading evidence. This preserves §11's requirement to grade the available artifact rather than infer its grade from the stop reason. The final interfaces describe the corrected semantics and distinguish candidate failures from invalid trajectories and infrastructure failures.

## Verification and limits

- The current production/test/interface files reviewed here match their exact contents at `cb53f99`.
- All ten entries in `docs/evidence/M0/tested-files.json` match the reviewed files.
- The behavior-only pre-fix receipt records **28 failed, 6 passed**; the supplemental regression receipt records **4 failed, 4 passed**, demonstrating the reward-1 regression before its correction.
- The final `docs/evidence/M0/review-round1-supplement-verification.json` records **38 focused tests passed**, **103 full-suite tests passed**, and a clean scoped whitespace check, all exit 0. The coordinator also reports a current 103-test passing run. I inspected the test code and receipts without repeating the suite.

These are contract/storage unit results. No worker isolation, provider execution, authenticated human review, qualification, optimizer update, or independent evaluation is established by this re-review. The downstream gates listed in the original review remain obligations of their owners, including source/partition joins, genuine token and reward provenance, full evaluation denominators and metric authenticity. A measured field passing schema validation is not proof of its empirical truth.

No further M0 change is requested by this scoped re-review. Only this report was written; product code, index, branch and commits were not changed.
