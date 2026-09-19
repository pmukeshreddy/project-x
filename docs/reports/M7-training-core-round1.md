# M7 training-core review delta

Addressed the three P1 boundaries in `docs/reviews/M7-training-core-round1.md`:

1. The group gate binds actual M4 receipt fields and publication evidence, accepts genuine pre-execution source-rejection zeros, and preserves their `source_inspection` scope. Promoting that scope to worker integration rejects.
2. Null candidate failures reject. Excluded peers need INVALID/INFRASTRUCTURE disposition and an exact M4 receipt establishing the null outcome; unsupported labels cannot silently alter the baseline. Original four records, scopes and costs remain intact. Runner pre-grading invalidation receipts are still an explicit future integration seam.
3. Native SkyRL conversion now uses actual eligible prompt counts via an independent configuration view, then coalesces exact native per-prompt boundaries into configured minibatches with a final remainder. It neither fills the assigned shape nor invents measured rows. The real trainer config remains unchanged on success or failure. Singleton groups remain in accounting and contribute no optimization rows.

Validation:

```sh
PYTHONPATH=src .venv-training-cpu/bin/python -m pytest tests/test_training_core.py tests/test_training_torch.py tests/test_training_data.py tests/test_training_state.py tests/test_training_skyrl.py -q
```

**38 passed**; output in `docs/evidence/M7/training-core-round1/focused-tests.txt`. The new data tests invoke actual M4 grading on malformed and missing source submissions using diagnostic tasks/admission/tokens; no worker is entered. Binding and scope tampering reject. The pinned-source tests execute exact SkyRL tensor conversion and boundary functions with real CPU Torch; trainer resources, batch container and zero-size DP padding are explicit doubles. Cases cover one eligible group after filtering, three eligible groups with a two-plus-one remainder, undersized minibatches, unchanged full batches, noncontiguous UIDs, conversion failure and retained accounting. Earlier tests continue to verify real CPU optimizer/update/checkpoint behavior.

Source: existing inert SkyRL audit at `f5bc3b78dfddfb352870d5d7430cd226e5785838`, `dataset/preprocess.py:193–306` and `trainer.py:848–967,1480–1526`. Exact reusable source snapshots and SHA-256 provenance are under `docs/evidence/M7/training-core-round1/source/`; tests verify their hashes before extraction. No retrieval, installation, GPU/model calls or dependency changes occurred. Initial regression runs failed at the old source-scope gate and missing conversion adapter before implementation.

Native SkyRL/Ray/FSDP/vLLM execution, distributed normalization/padding/optimizer/sync, runner admission/collection, native checkpoint publication/resume and the full training service remain subsequent work and unverified; this is a scoped review delta, not M7 completion or feature-learning evidence. Next runner work retains the agreed explicit frozen `case_seed`, consumes one actual M4 grade, and binds `RolloutRecord.run_id` directly to its unique M6 Registry `operation=run` job ID.
