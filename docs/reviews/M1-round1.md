# M1 round 1 full independent re-review

Reviewed the complete M1 correction range `52aaf16..f6b3ef2`, the product source, dedicated tests, interface, report, approved M0 v2 interface, actual v2 receipts and inventory evidence, and the binding M1/global requirements. I also reviewed the test-only timeout-fixture corrections through `18a735b`; they do not change production behavior. The earlier inert-Git boundary rechecks remain part of this review. Only this report was written.

**Specification compliance: CHANGES REQUIRED.** Six of the seven original findings are corrected for the documented scope, and the real Click squash reconstruction remains credible and honestly provisional. Rebase reconstruction still accepts a source change mapped to a different occurrence in the integrated commit, and its comparison work is not bounded against hostile repository content. Those failures violate the requirements to verify a rewritten span, reject ambiguous history, and keep source processing bounded.

**Implementation quality: CHANGES REQUIRED.** The Git subprocess boundary, durable provenance and chronology, B/H-bound license proof, redirect/deadline policy, split closure, source-role separation, and content idempotency are materially improved and supported by code and evidence. The rebase delta algorithm is both semantically under-bound and capable of unbounded controller CPU work. The timeout descendant fixture race has been corrected at `18a735b`, and its focused test passes; it is not a production cleanup regression.

## Findings

### P1 — Rebase mapping accepts the same byte edit at the wrong occurrence

Locations: `src/feature_rl/history/git.py:344-409` and `:451-478`; overclaim in `docs/reports/M1.md:62`; interface at `docs/interfaces-M1.md:33`.

`_line_delta` discards every equal region and the before/after positions returned by `SequenceMatcher`. `_delta_digest` consequently binds a text edit to its path, modes, tag, and added/removed bytes, but not to where that edit occurred or to any surrounding context. Two commits that replace identical text at different occurrences produce the same digest and are accepted as a source-to-integrated rewrite.

This narrow reproduction changes the first `x` in the source commit and the second `x` in the claimed integrated commit. Current `GitHistory.reconstruct` accepts the mapping:

```sh
PYTHONPATH=src:tests .venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from feature_rl.history import GitHistory
from test_history import init_repo, commit, git

with TemporaryDirectory() as raw:
    repo = init_repo(Path(raw))
    commit(repo, 'a.txt', 'section-one\nx\nsection-two\nx\n')
    git(repo, 'switch', '-c', 'feature')
    source = commit(repo, 'a.txt', 'section-one\ny\nsection-two\nx\n')
    git(repo, 'switch', 'main')
    target = commit(repo, 'target.txt', 'target\n')
    integrated = commit(repo, 'a.txt', 'section-one\nx\nsection-two\ny\n')
    result = GitHistory(repo / '.git').reconstruct(
        integrated,
        integration='rebase',
        source_head=source,
        source_commits=(source,),
    )
    print(result.baseline_commit == target)
    print(result.commit_mapping)
PY
```

Observed at `18a735b`:

```text
True
(('20261486a123be8a626bbbd4a2c82f1221303382', 'ddd5976f73c23234a9467c9f437a1827afe03732', '59895e03b0e4e0504ca3d751460c592f71d2dc9184a7c97a22b3d56af5571ce3'),)
```

The temporary commit IDs vary, while the false acceptance and digest are stable. This is a false provenance association, not merely a conservative false rejection. Correct the proof so an accepted mapping is bound to an unambiguous location/context in both parents. The pilot may reject uncertain context movement rather than map it to another occurrence. Preserve a positive case with genuinely rewritten commit IDs, and add this repeated-occurrence case as a rejection regression.

### P1 — Rebase delta computation has no operation deadline or safe work bound

Locations: `src/feature_rl/history/git.py:344-350`, `:366-409`, and `:465-471`; contradicted bounded-operation claims in `docs/interfaces-M1.md:28` and `docs/reports/M1.md:80`.

Each text blob may be as large as 16 MB, then `_line_delta` invokes `difflib.SequenceMatcher(..., autojunk=False)` in the controller process. The Git subprocess output cap and deadline have already ended at this point; neither `GitHistory.timeout_seconds` nor another deadline limits this pure-Python comparison. Repeated lines are a known worst case for this algorithm.

The coordinator diagnostic in `docs/evidence/M1/coordinator-repeated-line-diagnostic.json` used only a 20,004-byte trusted synthetic input (`b"x\n" * 10000 + b"end\n"`) and a four-byte prefix. `_line_delta` did not finish within three seconds and required external `SIGKILL`. Production admits blobs up to 16,000,000 bytes, so a hostile repository can consume unbounded CPU far beyond the advertised history deadline.

Bound the complete reconstruction computation, including in-process delta comparison, and fail closed on exhaustion. Use an algorithm with a defensible work bound or isolate it behind an enforceable deadline. Add a small repeated-line adversarial regression that completes with an explicit result inside the declared bound.

### P2 — Squash support is intentionally limited to B-rooted source chains but the limitation is not reported plainly

Locations: `src/feature_rl/history/git.py:436-450`, `docs/interfaces-M1.md:33`, and the incomplete limitation list at `docs/reports/M1.md:76`.

The corrected squash proof is safe for its declared topology: the complete source chain must start directly at B and its final tree must equal H. It rejects a disconnected equal-tree orphan. It also rejects an ordinary squash where the feature branch starts at an older target commit and the target advances before integration, because line 446 requires the source chain's first parent to equal H's immediate parent. A narrow fixture for that topology fails with `ValueError: declared commit chain is not contiguous`.

The pilot specification permits rejecting uncertain histories, so this is a supported-scope limitation rather than an unsafe acceptance. State plainly in the report that M1 does not provide comprehensive squash reconstruction and currently accepts only the documented B-rooted topology. Preserve a regression demonstrating rejection of the target-advanced topology so later changes do not silently broaden the proof. The real Click source chain is B-rooted and is unaffected.

## Original finding dispositions

| Original finding | Disposition |
| --- | --- |
| Repository-local Git helper execution | **Addressed.** Both diff paths pass `--no-ext-diff` and `--no-textconv`; the process environment disables system/global config, hooks, paging, prompting and replacement refs. Hostile-config fixtures cover reconstruction and object reads. The prior focused boundary review found no remaining helper execution path. |
| Promisor lazy fetch and mutation | **Addressed.** Every Git process receives `GIT_NO_LAZY_FETCH=1`; an incomplete promisor fixture fails without changing its object inventory. The two real cached Click attempts also leave the before/after object inventories byte-identical. |
| Durable provenance and preimplementation chronology | **Addressed.** `PullRequestIntakeSpec` and ingestion enforce cutoff/recording/retrieval/source/integration ordering. Historical provenance requires a pre-cutoff archived issue snapshot with consistent update metadata. CandidateRecord, SourcePair and the result carry the same required M0 v2 label. The Click run correctly remains `reconstructed_specification`. |
| Disconnected squash acceptance | **Addressed for the documented B-rooted topology.** The declared chain must start at B and finish at the source head, and the source/H trees must match. The narrower supported scope is the P2 disclosure item above. |
| Genuine rewritten rebase mapping | **Not addressed.** Distinct integrated SHAs and an ordered mapping now exist, but the mapping can bind a change to the wrong occurrence and can exceed all declared work bounds. |
| License text not bound to B/H | **Addressed.** The API object must match B, H must retain the same object, and bounded B/H blob reads must equal the cached authoring text before the verified license is emitted. |
| Redirect and whole-operation deadline | **Addressed.** Acquisition runs in a child under one monotonic parent deadline and byte cap; each redirect remains credential-free same-origin HTTPS, disallowed literal/local targets reject, and observed chains are archived. Legacy null chains remain explicitly unavailable evidence. |

The post-fix process cleanup is also satisfactory. Every Git command owns a process group; cleanup targets that group after success, command failure, timeout, and output-limit failure, verifies no live member remains, and fails closed on its bounded cleanup deadline. The original timeout test raced before its helper established a child: the coordinator preserved that failure in `docs/evidence/M1/coordinator-v2-final-suite-failure.json`. The test-only changes through `18a735b` wait, under a separate two-second setup bound, for a complete positive PID that names a live child and clean up setup failures. My focused reproduction at current HEAD passed in 0.54 seconds. No fresh full-suite receipt at `18a735b` was available during this review, so the report's `150 passing tests` statement is not current independent suite evidence.

## Real Click evidence and remaining gates

The two accepted v2 Click attempts used factory revision `dec81182a507a9e41dd80e38f2eba91c10564fc7`, exited 0 with empty stderr, and produced byte-identical output SHA-256 `c46030780dd1bed48792ede417bb676cc787fbaf211bf1d75f57523461c100f0`. Their object inventories are unchanged. The v2 CandidateRecord and SourcePair carry `reconstructed_specification`; B `19fd4d6e18bc9fce451f92f422696b11169faa57`, H `831c8f0948af519e45b90801d7430ff25451f972`, the B-rooted source chain, equal H/source tree, changed paths, and B/H license object are consistently recorded. The author role can read only request evidence, B and license text; H and the SourcePair remain private. Repeated cached ingestion is content-idempotent, costs remain explicit unknown/null, and the three mixed paths keep the candidate provisional.

The open rebase defects do not alter this squash-based Click result, but they block M1 approval as a general intake/history component. Runtime and human scope review remain downstream as specified; they are not M1 defects. After the rebase proof and work bound are corrected, rerun the focused adversarial cases and obtain a fresh current full-suite receipt before replacing the verification claim.
