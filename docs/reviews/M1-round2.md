# M1 round 2 independent re-review

Reviewed the complete correction range `18a735b..c2bbd3d`, focusing on the M1 source/test commit `a658ae4d0ffba45d1fbde6c4edad71da33ea570e`, the current M1 interface and report, `docs/evidence/M1/owner-round2-suite.json`, and the three open dispositions in `docs/reviews/M1-round1.md`. The later commits in the range are coordination and evidence changes. Only this report was written.

**Specification compliance: PASS for the M1 round-two scope.** The rewritten-rebase proof now rejects the demonstrated wrong semantic occurrence, accepts a genuine distinct rewrite and stable uniquely located line-number movement, and fails closed on ambiguity or exhausted work. The deliberately narrow B-rooted squash topology is now explicit and regression-tested. No open round-one specification finding remains in this scoped correction.

**Implementation quality: PASS for the M1 round-two scope.** The replacement proof uses inert Git diff data, exact path/mode/byte/context digests, ordered and unique mapping checks, one reconstruction deadline, and explicit commit/path/blob/diff/hunk/line/search caps. I found no regression in Git helper isolation, process-group cleanup, missing-object behavior, or the previously approved intake/source evidence paths.

## Finding dispositions

### P1 wrong-occurrence rewritten-rebase mapping — ADDRESSED

Locations: `src/feature_rl/history/git.py:514-535`, `:553-666`, and `:715-760`; tests at `tests/test_history.py:180-308`.

The old proof discarded positions and all unchanged context. The replacement asks inert Git for a path-scoped Myers diff with external diff, text conversion, renames, indent heuristics, color, and repository-selected algorithms disabled. For each text hunk it derives the exact removed and added lines plus the immediate unchanged line on each available side. `_unique_hunk_location` requires that combined local location to occur exactly once in both the parent and result blob. The proof digest also includes the exact path, before/after modes and object types; opaque or binary changes use exact before/after identities.

The ordered source and integrated proof digests must match. A separate action digest includes path/mode/type and exact removed/added bytes, and action digests must be unique across the source commit mapping. This preserves reordered, duplicate-action, whitespace, binary, mode, path, and interleaving rejection while retaining distinct source/integrated object IDs.

The original failure now rejects: changing the first `x` under `section-one` in the source cannot map to changing the second `x` under `section-two` in the integrated commit because their bound context differs. The positive target-insertion fixture also establishes the intended conservative allowance: adding `preface` moves the line number, while the unique `header` / changed line / `footer` location remains identical. Uncertain or repeated locations reject rather than falling back to a weaker digest.

I reran the wrong-occurrence rejection and unique-context line-movement cases at current HEAD; both passed.

### P1 unbounded rewritten-rebase computation — ADDRESSED

Locations: `src/feature_rl/history/git.py:22-31`, `:76-94`, `:195-264`, `:327-407`, `:436-551`, `:553-666`, and `:668-790`; interface disclosure at `docs/interfaces-M1.md:28-41`.

`SequenceMatcher(autojunk=False)` is removed. `reconstruct` creates one monotonic deadline and passes it through commit reads, graph traversal, path enumeration, blob reads, rewrite diffs, integrated-span recovery, and the final B-to-H patch digest. `_run_bytes` takes the earlier of its command deadline and the reconstruction deadline, retains separate stdout/stderr caps, and still executes its verified process-group cleanup in `finally`. Controller loops and budget charges check the same deadline; the final patch-digest step prevents a reconstruction from returning success after the deadline.

The rewritten-rebase path now has explicit limits of 128 mapped commits, 256 changed paths, 16,384 UTF-8 bytes per path, 1,000,000 bytes per blob, 8,000,000 cumulative blob bytes, 8,000,000 diff/path bytes, 1,024 hunks, 500,000 split lines, and 2,000,000 location-comparison units. Potentially larger parsing/hashing steps are bounded by the preceding byte caps. Budget or deadline exhaustion raises explicitly and never downgrades to byte-only or patch-ID matching.

The former 20,004-byte repeated-line case now reaches a bounded ambiguity/work-budget rejection without invoking `SequenceMatcher`. I reran that regression together with the two semantic mapping cases and the squash-scope case: **4 passed in 2.65 seconds**. The checked owner receipt binds the exact product/test bytes at `a658ae4` to the full suite at **155 passed in 17.30 seconds**, a seven-case focused mapping bundle, and a four-case budget bundle, all with exit status 0.

### P2 narrow squash support disclosure — ADDRESSED

Locations: `src/feature_rl/history/git.py:696-714`, `tests/test_history.py:128-149`, `docs/interfaces-M1.md:33`, and `docs/reports/M1.md:77`.

The implementation continues to accept only a complete source chain rooted directly at B whose final tree equals H. It rejects both a disconnected equal-tree source and a feature rooted before a target advance. The interface and report now name this as conservative pilot scope rather than implying comprehensive squash recovery, and the target-advanced regression preserves the rejection. The real Click source chain is B-rooted, so this limitation does not weaken its preserved squash proof.

## Regression and evidence assessment

The round-two product delta is confined to Git history reconstruction and its tests. Repository-local helper defenses, `GIT_NO_LAZY_FETCH`, bounded stdout/stderr, and descendant cleanup remain intact. Exact path/mode/object/byte evidence is stronger than before; no authoring-visible or private source role changed. The current report correctly keeps the accepted Click v2 attempts bound to factory revision `dec81182a507a9e41dd80e38f2eba91c10564fc7` and does not relabel them as round-two outputs.

A coordinator-owned cached Click run at the current revision remains a post-review verification gate. Because Click uses the separately proven B-rooted squash path rather than rewritten rebase, the absence of that current-revision receipt is not a defect in this source correction and does not reopen the preserved Click evidence. M1 round two is cleared for that coordinator run.
