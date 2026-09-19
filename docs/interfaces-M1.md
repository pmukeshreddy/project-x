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

`BoundedHttpFetcher(max_bytes: int, timeout_seconds: float).fetch(url, *, edit_history, published_at=None, edited_at=None, media_type=None) -> FetchedSource` accepts credential-free HTTPS URLs only. It passes the deadline to the HTTP opener, rejects non-2xx responses, checks both declared and streamed body size, and returns the exact response bytes with UTC retrieval and edit metadata. Failures are explicit: `SourceFetchError`, `SourceFetchTimeout`, or `SourceTooLarge`.

`CachedSourceCatalog(root: Path, manifest: Path, *, max_bytes: int).load(name, *, edit_history, published_at=None, edited_at=None, media_type="application/json") -> FetchedSource` reuses an inert response cache. The tab-separated manifest columns are exactly `name`, `http_status`, `retrieved_at`, `url`, `body_path`, and `sha256`. The loader confines paths to `root`, rejects duplicate or malformed rows and unsuccessful responses, enforces the byte limit, and verifies each body digest. Integrity failures raise `SourceIntegrityError`.

`SourceArchiver(store: ArtifactStore).archive(source, visibility) -> SourceSnapshot` stores the exact body as a M0 `source-response` byte artifact and binds its URL, timestamps, media type, and edit-history status. `verify(snapshot) -> bytes` reads through `ArtifactStore`, including its access and digest checks.

## Git reconstruction and changed-file classification

```python
from feature_rl.history import GitHistory, classify_changed_files
```

`GitHistory(git_dir: Path, *, timeout_seconds: float = 30.0)` opens an existing non-symlink Git object database. Every Git subprocess is read-only, has hooks disabled, ignores replace refs, disables prompts and optional locks, uses an explicit deadline, and caps output. It never checks out, imports, builds, or executes source content.

The main methods are:

- `commit(revision) -> CommitObject`: resolve a full object ID and return its tree and parents.
- `reconstruct(integrated_after, *, integration, source_head=None, implementation_commits=()) -> Reconstruction`: derive B and H from the graph. A normal merge uses H's first parent as B and verifies its second parent. A squash requires the declared source head tree to equal H's tree. A rebase or linear integration requires an explicit contiguous one-parent span ending at H. Unknown, missing, mismatched, ambiguous, or interleaved histories are rejected.
- `changed_paths(baseline, reference) -> tuple[str, ...]`: return the NUL-delimited B-to-H path set.
- `path_object(revision, path) -> str`: resolve a safe repository-relative path to its Git object ID.
- `archive_tree(revision, *, max_bytes) -> bytes`: return a bounded tar archive produced directly from the commit tree, without `.git` metadata or future history.

Invalid graph claims raise `ValueError`; missing or malformed objects raise `UnrecoverableHistory`; timeouts raise `HistoryError`; excessive output raises `HistoryOutputLimit`.

`classify_changed_files(paths, *, mixed_paths=None) -> ClassificationResult` returns M0 `ChangedFile` values plus a sorted `manual_review_required` list. Explicit mixed-path reasons override path-based classification. Unknown paths stay `mixed`; M1 does not silently classify them as unrelated or automatically acceptable.

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

`PullRequestIntakeSpec` names the cached PR, issue, comment, commit, changed-file, license, and license-text responses; declares the repository family and request lineage; supplies the integration method, UTC admissible cutoff and recording time, provenance label, mixed-file reasons, and B-tree archive cap. `recorded_at` is caller-supplied so an identical retry is content-idempotent.

`GitHubPullRequestIntake.ingest(spec, partition_manifest) -> PullRequestIntakeResult` requires one explicit partition for the full source closure. It then:

1. verifies and archives the named response bodies;
2. derives B/H and implementation commits from Git rather than trusting current base/head API fields;
3. cross-checks the PR commit list, changed-file response, and `LICENSE.txt` Git blob;
4. emits a private M0 `CandidateRecord` and private M0 `SourcePair` with real evidence and unknown, nullable cost fields;
5. emits an `AuthoringSourceView` containing only the authoring-visible request evidence, B tree archive, and license text; and
6. returns the private H tree archive separately for the controller.

The connected result fields are `candidate`, `source_pair`, `authoring`, `reference`, and `manual_review_required`. M2 receives only `result.authoring` when deriving a requirement contract. M3 may consume `result.authoring.baseline`. A controller or later privileged stage retains `candidate`, `source_pair`, and `reference`. An author role cannot read the private `SourcePair` or H archive through `ArtifactStore`.

For `reconstructed_specification`, the derived request bundle retains the provenance label and caveat. A comment enters the cutoff view only when both its creation and last-update times are at or before the admissible cutoff. H IDs, source implementation commit IDs, diffs, and later comments are absent from that view. M1 does not author a `RequirementContract` or decide whether a mixed-purpose change belongs in one.

## Reproduce the Click evidence

The command below uses the preserved ignored cache at `.feature-rl/research/M1`; it makes no network request and executes no Click source:

```sh
PYTHONPATH=src .venv/bin/python docs/evidence/M1/run_real_intake.py \
  --workspace . \
  --factory-revision 2c801f69a8aac8a2a922191f7a2f5e957435277a \
  --recorded-at 2026-09-19T08:07:10Z
```

The checked evidence output is `docs/evidence/M1/real-intake.json`. Raw public responses, bare Git objects, and immutable production artifacts remain under ignored `.feature-rl/research/M1` because they include privileged H and must not enter a solver-visible repository export.
