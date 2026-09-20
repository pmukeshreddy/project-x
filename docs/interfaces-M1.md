# M1 source intake, history, and split interfaces

M1 consumes public source responses and an existing bare Git object database as inert data. It emits the reviewed M0 `CandidateRecord` and `SourcePair` artifacts plus a deliberately smaller authoring view. Import the public entry points from `feature_rl.intake`, `feature_rl.history`, and `feature_rl.splits`; artifact schemas and storage remain owned by `feature_rl.contracts` and `feature_rl.artifacts`.

## Source acquisition and archiving

```python
from feature_rl.intake import (
    BoundedHttpFetcher,
    CachedSourceCatalog,
    FetchedSource,
    SourceArchiver,
)
```

`BoundedHttpFetcher(max_bytes: int, timeout_seconds: float).fetch(url, *, edit_history, published_at=None, edited_at=None, media_type=None) -> FetchedSource` accepts credential-free HTTPS URLs without local-host or nonpublic literal-IP targets. Redirects must remain on the requested HTTPS origin. The complete DNS/connect/redirect/read operation runs in a child process under one monotonic deadline; expiry kills and joins that process. Bodies are streamed under the byte cap after the declared length is checked. The result contains the exact bytes, UTC retrieval/edit metadata, and the observed redirect chain. Failures are explicit: `SourceFetchError`, `SourceFetchTimeout`, or `SourceTooLarge`.

`CachedSourceCatalog(root: Path, manifest: Path, *, max_bytes: int).load(name, *, edit_history, published_at=None, edited_at=None, media_type="application/json") -> FetchedSource` reuses an inert response cache. The tab-separated manifest columns are exactly `name`, `http_status`, `retrieved_at`, `url`, `body_path`, and `sha256`. The loader confines paths to `root`, rejects duplicate or malformed rows and unsuccessful responses, enforces the byte limit, and verifies each body digest. These legacy manifests did not capture redirect hops, so loaded values use `redirect_chain=None`; this means unavailable evidence and never asserts that no redirect occurred. Integrity failures raise `SourceIntegrityError`.

`SourceArchiver(store: ArtifactStore).archive(source, visibility) -> SourceSnapshot` stores the exact body as a M0 `source-response` byte artifact and binds its URL, timestamps, media type, edit-history status, and known or unavailable redirect chain. `verify(snapshot) -> bytes` reads through `ArtifactStore`, including its access and digest checks.

## Git reconstruction and changed-file classification

`feature-rl prepare-github --request <GitHubPreparationRequest.json> --output <absolute-directory>`
captures one explicitly selected repository PR and issue for the existing
cached intake. The request type and `prepare_github(request, Path)` are in
`feature_rl.intake.prepare`. See the [runbook](runbook.md) for required fields and
the direct `construct-feature --github ... --capture ...` and `--prepared ...`
paths. Optional `token_env` selects an in-memory `GH_TOKEN` or `GITHUB_TOKEN` for
private repositories; credentials are never written into the capture or Git
configuration. Offline reuse does not load credentials. Preparation reuses `BoundedHttpFetcher`,
`CachedSourceCatalog` and M1's bounded Git process runner and reconstruction proof.
It initializes an empty bare repository with no templates, fetches only the
selected immutable head/integration revisions to a declared depth, and performs
no checkout, submodule fetch, repository hook, model call or task admission.

Every HTTP page is retained as a separate body with its actual URL, retrieval
time and SHA256. Pagination counts and duplicate identities are checked, PR/issue
metadata is checked again, and commit/file lists are checked against Git. The
implementation rejects PRs beyond the API's [250-commit](https://docs.github.com/en/rest/pulls/pulls#list-commits-on-a-pull-request)
and [3,000-file](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files)
limits; a lower configured page/byte limit can reject earlier. License metadata
and bytes are fetched at the derived baseline revision and must match both B and
H. Captures are labeled `reconstructed_specification`, since a fresh download
cannot establish preimplementation edit history. Completed captures retain their
recording time and pinned manifest on reuse; failed/partial directories are not
silently refreshed. Git transfer is limited by time and ancestry depth, not an
aggregate pack-file disk quota. The capture is preparation evidence, not proof of
a buildable runtime or a qualified task.

```python
from feature_rl.history import GitHistory, classify_changed_files
```

`GitHistory(git_dir: Path, *, timeout_seconds: float = 30.0)` opens an existing non-symlink Git object database. Every Git subprocess is read-only, has hooks, external diff, text conversion, paging, prompting, replace refs, optional locks, and promisor lazy fetching disabled. stdout and stderr have separate caps. A reconstruction shares one monotonic `timeout_seconds` deadline across all Git commands and controller-side proof work; other public operations retain bounded Git-command deadlines. Each command starts in an owned process group. Cleanup signals that group even after its leader exits, verifies that no live member remains, and fails closed after a bounded condition loop. On this macOS host `killpg` can return `PermissionError`; the tested fallback enumerates that owned group with `/bin/ps`, kills each live member directly, ignores only zombie states, and repeats verification. It never checks out, imports, builds, or executes source content.

The main methods are:

- `commit(revision) -> CommitObject`: resolve a full object ID and return its tree, parents, and repository-asserted author/committer timestamps.
- `reconstruct(integrated_after, *, integration, source_head=None, implementation_commits=(), source_commits=()) -> Reconstruction`: derive B and H from the graph. A normal merge uses H's first parent as B and verifies its second parent. Squash support is deliberately limited to a complete source chain rooted directly at B whose final tree equals H; a feature rooted before a target advance rejects. A linear integration requires an exact contiguous span ending at H. A rebase derives an equally sized contiguous integrated span ending at H and requires distinct source and integrated object IDs. For each text hunk it binds the exact path, modes, removed and added bytes, and the immediate unchanged line on each available side; that combined location must occur exactly once in both the parent and result blobs. This allows line-number movement when the local context remains stable and unique, while a byte-identical edit under a different section rejects. Opaque and binary changes require exact before/after byte identity. Repeated semantic actions, reordered or interleaved spans, whitespace changes, uncertain context, and budget exhaustion reject. `Reconstruction.commit_mapping` records each accepted source/integrated/context-proof triple.
- `changed_paths(baseline, reference) -> tuple[str, ...]`: return the NUL-delimited B-to-H path set.
- `path_object(revision, path) -> str`: resolve a safe repository-relative path to its Git object ID.
- `path_bytes(revision, path, *, max_bytes) -> bytes`: read a bounded blob directly from the local object database without filters or lazy acquisition.
- `archive_tree(revision, *, max_bytes) -> bytes`: return a bounded tar archive produced directly from the commit tree, without `.git` metadata or future history.

Invalid graph claims raise `ValueError`; missing or malformed objects raise `UnrecoverableHistory`; timeouts raise `HistoryError`; excessive output raises `HistoryOutputLimit`.

Rewritten-rebase proof is a conservative pilot surface. One operation admits at most 128 mapped commits, 256 changed paths, 16,384 UTF-8 bytes per path, 1,000,000 bytes per blob, 8,000,000 total blob bytes, 8,000,000 diff/path bytes, 1,024 hunks, 500,000 split lines, and 2,000,000 bounded location-comparison units. Git diff runs inside the shared deadline with external diff, text conversion, renames, indent heuristics, and repository-selected algorithms disabled. Exhausting any deadline or work budget fails closed; it does not downgrade to a weaker patch-ID or byte-only association.

`classify_changed_files(paths, *, mixed_paths=None) -> ClassificationResult` returns M0 `ChangedFile` values and a sorted uncertainty list under the legacy name `mixed_paths_for_qualification`. Test/fixture files under `test/` or `tests/` components (including nested directories), `test_*` files and `conftest.py` are tests. Top-level `doc/`, `docs/`, `example/` and `examples/`, GitHub issue/PR template directories, Markdown/reStructuredText and conventional extensionless or `.txt` README/changelog/contributor files are documentation. Dependency/build names, requirements text files and GitHub workflows take precedence over directory rules. Explicit mixed-path reasons override automatic categories, and unknown paths remain `mixed`.

New intake does not make those labels a manual approval prerequisite: it retains
them in `SourcePair` and the reconstruction proof's `mixed_paths_for_qualification`,
returns an empty intake `mixed_paths_for_qualification`, and screens only source/history
and license validity. M5 independently projects permitted source changes onto B,
recording included/excluded paths and hashes, then requires the actual projected
reference to pass qualification. Excluded configuration/assets stay at B; no new
candidate write permissions or dependency changes are authorized. Frozen older
candidate dispositions are not rewritten.

## Leakage-safe partition closure

```python
from feature_rl.splits import Relation, SplitPlanner

manifest = SplitPlanner(relations).assign(requested_partitions)
```

Each `Relation(left, right, kind, proof)` retains factual proof. `fork`, `backport`, `copied_code`, `monorepo`, `descendant`, and `same_request` join endpoints into one deterministic component. An explicit assignment on any member propagates to the component. Conflicting assignments raise `SplitConflict`. `dependency` is retained in `PartitionManifest.review_required` and does not by itself prove either sameness or independence. Equal inputs produce equal, sorted assignments and component IDs.

## Connected pull-request intake

```python
from feature_rl.intake import GitHubPullRequestIntake, PullRequestIntakeSpec

intake = GitHubPullRequestIntake(
    store=controller_store,
    catalog=catalog,
    history=history,
    factory_revision=factory_revision,
)
result = intake.ingest(spec, partition_manifest)
```

`PullRequestIntakeSpec` names the cached PR, issue, comment, commit, changed-file, license, and license-text responses; declares the repository family and request lineage; supplies the integration method, UTC admissible cutoff and recording time, provenance label, mixed-file reasons, and B-tree archive cap. `recorded_at` is caller-supplied so an identical retry is content-idempotent. Construction rejects a cutoff after recording. Ingestion additionally requires issue creation ≤ cutoff ≤ first source author/committer time ≤ integration time ≤ recording time, and every retrieval time ≤ recording time. Git timestamps are retained as repository assertions rather than independent clock attestations.

Optional `additional_pages` maps a comments/commits/files primary source name to
its ordered additional source names (up to 29 additional pages each). All pages
must occur exactly once in `source_names`, and cannot alias another primary source
or another page. Intake archives each original body and combines arrays only in
memory; duplicate comment IDs, commit SHAs or filenames reject. Existing
single-response specs work with the default empty mapping.

`GitHubPullRequestIntake.ingest(spec, partition_manifest) -> PullRequestIntakeResult` requires one explicit partition for the full source closure. It then:

1. verifies and archives the named response bodies;
2. derives B/H and implementation commits from Git rather than trusting current base/head API fields;
3. cross-checks the PR commit list and changed-file response, and requires the cached license bytes and API object to equal `LICENSE.txt` in both B and H;
4. emits a private M0 v2 `CandidateRecord` and private M0 v2 `SourcePair` with the same validated provenance label, real evidence, and unknown, nullable cost fields;
5. emits an `AuthoringSourceView` containing only the authoring-visible request evidence, B tree archive, and license text; and
6. returns the private H tree archive separately for the controller.

The connected result fields are `candidate`, `source_pair`, `authoring`, `reference`, `mixed_paths_for_qualification`, and `provenance_label`. The label equals the required value stored in both typed v2 artifacts. M2 receives only `result.authoring` when deriving a requirement contract. M3 may consume `result.authoring.baseline`. A controller or later privileged stage retains `candidate`, `source_pair`, and `reference`. An author role cannot read the private `SourcePair` or H archive through `ArtifactStore`.

`historical_request` is admitted only when the exact archived issue snapshot was retrieved no later than the preimplementation cutoff and its update metadata does not postdate that snapshot. Otherwise callers must use `reconstructed_specification`, whose request bundle retains the label and caveat. A comment enters the cutoff view only when its creation and last-update times are ordered and at or before the cutoff. H IDs, source implementation commit IDs, diffs, and later comments are absent from that view. M1 does not author a `RequirementContract` or decide whether a mixed-purpose change belongs in one.

## Reproduce the preserved Click evidence

The command below uses the preserved ignored cache at `.feature-rl/research/M1`; it makes no network request and executes no Click source:

```sh
PYTHONPATH=src .venv/bin/python docs/evidence/M1/run_real_intake.py \
  --workspace . \
  --factory-revision dec81182a507a9e41dd80e38f2eba91c10564fc7 \
  --recorded-at 2026-09-19T08:53:03Z
```

The accepted checked outputs and per-attempt receipts are `docs/evidence/M1/real-intake-v2-attempt-{1,2}.json` and `real-intake-v2-attempt-{1,2}-receipt.json`. They prove the Click B-rooted squash path at the named revision; they are not presented as outputs of the later rewritten-rebase hardening revision. The original v1 receipt and one explicitly invalid v2 revision receipt are preserved separately. Raw public responses, bare Git objects, and immutable production artifacts remain under ignored `.feature-rl/research/M1` because they include privileged H and must not enter a solver-visible repository export. A coordinator-owned current-revision cached run remains required after round-two review.
