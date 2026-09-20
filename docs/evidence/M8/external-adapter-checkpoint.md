# M8 external-row adapter checkpoint

This CPU-only checkpoint implements bounded ingestion for already supplied private SWE-Bench++ rows. It acquired no dataset payload, repository, model, task or human approval.

The adapter:

- enforces the exact visible pinned row schema and immutable dataset/harness revisions;
- requires an explicit origin mapping to actual M1 `CandidateRecord`/`SourcePair` provenance because the release lacks PR/H metadata;
- validates repository family, lineage, local train/development assignment, B/reference commits, changed paths, rights/use classification and content-dedup digests before M6 screening;
- keeps solution/test patches, native test names, H and environment hints private while emitting a positive PUBLIC/AUTHORING source allowlist;
- invokes actual `Factory.screen_source` and records its selected disposition/costs in a complete per-item funnel; and
- registers the opaque configuration, frame, mappings, row leaves, M6 receipts and private batch in the existing Registry.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_external_adaptation.py -q
```

Result before commit: `2 passed in 0.82s`.

The combined owned diagnostic set was also observed as `23 passed in 1.27s`; no broad suite or Docker matrix was repeated. These are labeled synthetic row/mechanism checks, not corpus coverage or task results.

No actual row was adapted. No item is claimed runnable or qualified. This slice stops after actual M6 source screening because the construction input for an external row must come from real M2/M3/M4 artifacts; it cannot be manufactured from `test_patch`, native tests or untrusted `environment_config`. Factory construction, fresh-worker validation, external observations, human qualification, release, training and GPU evaluation remain blocked by absent real inputs.
