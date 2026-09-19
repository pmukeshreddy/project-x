# M1 bounded Click PR 3228 investigation

Investigation date: 2026-09-19 UTC

This is a factual source-investigation record, not a feature contract, acceptance decision, `CandidateRecord`, or `SourcePair`. The raw public responses, Git object cache, complete B/H source archives, and generated diff are under the ignored path `.feature-rl/research/M1/`. They must remain outside any solver-visible export. No Click code was imported or executed, and no repository hook, build, dependency installation, or test command was run.

## Result

Click PR [#3228](https://github.com/pallets/click/pull/3228) was integrated by a squash commit. The defensible pair is:

- **B:** `19fd4d6e18bc9fce451f92f422696b11169faa57`
- **H:** `831c8f0948af519e45b90801d7430ff25451f972`
- **B tree:** `82e09e778456a399fb9819c0362f3dca06358987`
- **H tree:** `1e65e01d5ddd281d5c9f4019cced263ceb6b43a6`

The current request text is archived, but a defensible preimplementation edit history for the issue or PR body is not available from the inspected public interfaces. The candidate must therefore use **reconstructed-specification** provenance unless later evidence proves a historical cutoff. It must not be labeled a historical request based only on the current body.

## Archived sources

`.feature-rl/research/M1/source-manifest.tsv` records each URL, retrieval timestamp, HTTP status, response-body path, and SHA-256 digest. `.feature-rl/research/M1/source-files.sha256` inventories the complete research directory. Key raw sources include:

| Evidence | Archived path |
| --- | --- |
| PR metadata and current body | `.feature-rl/research/M1/http/pr-3228.json` |
| Issue metadata and current body | `.feature-rl/research/M1/http/issue-3107.json` |
| PR/issue timelines and comments | `.feature-rl/research/M1/http/issue-3228-timeline.json`, `issue-3107-timeline.json`, and comment responses in the same directory |
| PR commits and changed files | `.feature-rl/research/M1/http/pr-3228-commits.json`, `pr-3228-files.json` |
| GitHub patch and diff | `.feature-rl/research/M1/http/pr-3228-patch.patch`, `pr-3228-diff.diff` |
| Integration commit and B-to-H comparison | `.feature-rl/research/M1/http/commit-integration-831c8f0.json`, `compare-base-integration.json` |
| License metadata and text | `.feature-rl/research/M1/http/license.json`, `license-main.txt` |
| CI API snapshots | `check-runs-*`, `check-suites-*`, and `status-*` in `.feature-rl/research/M1/http/` |
| Bare Click cache | `.feature-rl/research/M1/git/click.git` |
| Full B and H tree archives | `.feature-rl/research/M1/git-archives/click-B-19fd4d6e18bc9fce451f92f422696b11169faa57.tar`, `click-H-831c8f0948af519e45b90801d7430ff25451f972.tar` |
| Exact B-to-H binary diff | `.feature-rl/research/M1/git-archives/click-B-to-H.patch` |
| Git command evidence | `.feature-rl/research/M1/git-logs/ancestry-proof.log`, `content-digests.log`, `ci-config-snapshots.log` |

All HTTP bodies have their original response headers, requested URL, and UTC retrieval time beside them. The archive also preserves failed or superseded retrieval attempts under `http/attempts/` and the initial shallow-fetch failure in `git-logs/`; these are operational evidence rather than candidate facts.

## Request metadata and provenance

PR 3228 was created at `2026-02-23T16:10:36Z`, merged and closed at `2026-04-29T23:53:15Z`, and reports its last general update at `2026-05-15T00:45:46Z`. Its current title is “Add `NoSuchCommand` exception with suggestions for misspelled commands.” Its current body describes command suggestions, the new public exception, option-error formatting changes, movement of option suggestion calculation, normalized command names, examples, and a test checkbox. The current labels are `docs` and `help output`; they are discovery hints only.

Linked issue [#3107](https://github.com/pallets/click/issues/3107) was created at `2025-10-13T12:17:36Z`, closed as completed at `2026-04-29T23:53:16Z`, and reports its last general update at `2026-05-15T00:45:40Z`. Its current body requests `Did you mean` suggestions for misspelled subcommands, gives an example, identifies Click 8.3.0 and Python 3.13.8, and links older work. Its current labels are also `docs` and `help output`.

Available edit evidence does not establish an immutable historical body:

- The public timelines expose post-merge title edits on both records on 2026-04-30 and label changes, but no body revisions.
- The archived issue HTML supplies a current `bodyVersion` value, but no revision sequence for the root issue body. The public `/issues/3107/edits` request returned 404.
- One issue comment, created at `2026-01-15T20:36:27Z`, has `updated_at` and public `lastEditedAt` of `2026-01-15T20:38:10Z`; only its current text was recovered.
- The public `/pull/3228/edits` request redirected to the ordinary PR page and did not expose prior PR-body revisions.
- The first retained implementation commit has author time `2026-02-21T17:26:34Z`, two days before the PR was opened. The PR body is therefore implementation-aware even if it was never edited after opening.
- Eleven issue comments are archived. The final preimplementation discussion on 2026-02-13 leaves normalized-versus-original command-name behavior to the PR review rather than specifying it.

These facts require reconstructed-specification provenance. Current issue text and preimplementation comments may be cited as evidence with the edit-history limitation disclosed; later PR text, code, tests, and changelog may help reconstruct a requirement, but cannot be presented as an untouched preimplementation request.

## Exact integration and squash proof

GitHub metadata identifies `831c8f0948af519e45b90801d7430ff25451f972` as the merge commit. Its Git commit object has exactly one parent, `19fd4d6e18bc9fce451f92f422696b11169faa57`, so it is a squash integration rather than a normal two-parent merge.

The final PR source chain is:

1. `f6da6a7470dcc222513675d47345707710596488`, parent `19fd4d6e18bc9fce451f92f422696b11169faa57`, message “Add NoSuchCommand exception with suggestions for misspelled commands”.
2. `dc3e1e0294f7bee639fb170112f87d9ddbe51db5`, parent `f6da6a7470dcc222513675d47345707710596488`, message “Added CHANGES entry.”

The final PR head tree and H tree are both `1e65e01d5ddd281d5c9f4019cced263ceb6b43a6`. `git diff --exit-code H PR_HEAD --` exited 0. The merge base of B and the final PR head is B. GitHub's B-to-H comparison reports one commit ahead, zero behind, merge base B, and the same seven changed files. Together, the one-parent integration object, PR source ancestry rooted at B, and identical final trees provide the required squash binding.

The PR timeline records two base-ref changes and a force-push of the head on 2026-04-29. This is additional reason not to infer B from today's PR base/head fields alone. The graph evidence above determines B and H.

Content digests retained for later intake are:

| Artifact | SHA-256 |
| --- | --- |
| Full B tree tar | `9de5108a0e639b8e502117d4fbe955cb2dd2cb38319fc853fde3c222c16eb595` |
| Full H tree tar | `06f2e59b0b2a8d709062f625cd0825232bb871299e678a5e1f09055a0760c221` |
| Exact B-to-H patch | `868c9441243952215fb0c5c874209817111d080124c6cb4d22f9299d4f3be6e6` |

## Changed-file classification

The integrated diff has 86 additions and 22 deletions across seven files.

| File | Primary category | Factual scope and review status |
| --- | --- | --- |
| `CHANGES.rst` | Public documentation | Adds the release note for `NoSuchCommand` and command suggestions. |
| `src/click/__init__.py` | Implementation | Exports `NoSuchCommand` as public API. |
| `src/click/core.py` | Implementation | Replaces the generic failure with `NoSuchCommand` and supplies group commands as candidates. |
| `src/click/exceptions.py` | Implementation, mixed purpose | Adds shared suggestion formatting and `NoSuchCommand`, while also changing `NoSuchOption` formatting, accepted iterable type, and where close matches are calculated. Manual scope review is required. |
| `src/click/parser.py` | Implementation, mixed purpose | Moves option close-match calculation into `NoSuchOption`; this is a companion refactor/behavior change rather than the issue's core command-suggestion request. Manual scope review is required. |
| `tests/test_commands.py` | Tests | Adds unknown-command and one/multiple suggestion coverage. |
| `tests/test_options.py` | Tests, mixed purpose | Updates expected option formatting and suggestion wording. Manual scope review is required with the corresponding implementation changes. |

No dependency/build file changed. No separate unrelated file was found in the seven-file diff. That does not automatically resolve the mixed-purpose changes: inclusion of the option-formatting/refactor portion in H or in a future visible specification needs an explicit code/manual-review decision.

## License and CI snapshots

GitHub reports `BSD-3-Clause`. `LICENSE.txt` has Git blob `d12a849186982399c537c5b9a8fd77bf2edd5eab` in B and H, matching the current license API object. The archived raw license text has SHA-256 `9a8ad106a394e853bfe21f42f4e72d592819a22805d991b5f3275029292b658d`.

The B and H `.github` tree object is identically `7e5f73ce9c4b02fd5ae04ccbd7e8f2668162228f`. The five workflow blob IDs for `lock.yaml`, `pre-commit.yaml`, `publish.yaml`, `tests.yaml`, and `zizmor.yaml` are identical across B and H. The `.readthedocs.yaml`, `.pre-commit-config.yaml`, `pyproject.toml`, and `uv.lock` objects are also identical; exact IDs are in `git-logs/content-digests.log` and the CI archives are in `git-archives/`.

At retrieval, the final PR head exposed 11 completed successful GitHub Actions check runs: `main`, `typing`, Python 3.10, 3.11, 3.12, 3.13, 3.14, 3.14t, PyPy, Windows, and Mac. Its current combined status also contains a successful Read the Docs status created on 2026-07-29, after merge. H exposed 12 completed successful GitHub Actions checks (the same set plus `lock`), while a Read the Docs check suite remained queued and the legacy combined status had no status entries. These are mutable API snapshots taken on 2026-09-19, not proof of the exact required-check policy at merge time. The unauthenticated branch-protection query returned 401 and is preserved as a failed attempt.

## Repository family and request lineage facts

The base repository is `pallets/click`. The final PR head is from the fork `RC2215/click`; the fork and upstream must be treated as one repository family. PR 3228 closes issue 3107, so both records belong to one request lineage and must receive the same future partition assignment. The issue timeline also cross-references older same-repository attempts/discussions and an external Cloup implementation. Those references are relation-review leads, not proof of independence and not separately collected in this bounded investigation.

The brief requires this candidate and linked request to be assigned to train before generation. This investigation records the closure facts but intentionally does not instantiate a partition/schema record while M0 review is pending.

## Unresolved gates and cost

- **Provenance:** reconstructed-specification is required unless a defensible issue-body revision history and preimplementation cutoff are later recovered.
- **Mixed files:** the `NoSuchOption` formatting/refactor changes and their tests require explicit manual/code review before classifying them as legitimate support or excluding them from the task scope.
- **CI history:** current checks and immutable workflow blobs are archived, but the exact historical required-check/branch-protection policy is unavailable.
- **Candidate acceptance:** no acceptance decision is made here; labels are not acceptance evidence.
- **Cost:** monetary, token, and human-review costs are **unknown** because this bounded investigation did not have an intake cost recorder. Do not encode them as zero. No paid service or compute was provisioned.

The archive is ready to be reused by the later implemented intake path. Future B-only authoring export must be produced from the B tree archive and admissible request evidence only; it must exclude H, the B-to-H diff, final implementation/test evidence, later Git objects, and this investigator's conclusions about the solution.
