# M0 optional MLX dependencies independent review

Reviewed base `3761b3f` through `18bd8f34ad531ad7570ba1942e45bf9eaca518c5`, including the supplied complete review package, `pyproject.toml`, `uv.lock`, `requirements-authoring-mlx.lock`, the M0 dependency report and its five evidence files. Independently compared the approved M2 acquisition manifest and actual local wheel bytes/metadata. Only this report was written; no package installation, product edit, index/commit change, model download, inference or full-suite rerun was performed.

**Specification compliance: PASS for this dependency/configuration slice.** The exact approved 34-wheel closure is pinned and opt-in, existing core/dev versions and project Python scope are retained, and supported-platform limits and downstream preflight responsibility are explicit.

**Implementation quality: PASS for this scope.** No actionable severity finding identified. The verified wheel-only installation path is reproducible from retained local artifacts, and the report distinguishes dependency loading from model inference or training qualification.

## Independent checks

- Parsed both TOML files and the hash-required requirements file. All **34** normalized package names and exact versions agree with `.feature-rl/research/M2/dependency-lock.json`; every approved wheel hash appears in its matching `uv.lock` package entry and matches the sole hash pinned for that package in `requirements-authoring-mlx.lock`.
- Rehashed all **34 local wheels**, totaling **96,889,626 bytes**, against the acquisition manifest's SHA-256 and size. All matched. Read wheel METADATA as inert ZIP contents and evaluated active dependency requirements for CPython 3.13/macOS arm64: the approved closure satisfies every active requirement with no missing package or version mismatch.
- Compared the base and final configuration. `[project]`, `[build-system]` and the dev requirement are unchanged. Every pre-existing non-root locked package entry is identical, including distribution hashes, and root runtime dependencies are unchanged. All **12** pre-existing core/dev/root package versions are retained. The added optional group does not become a project runtime dependency or a default group.
- Evaluated the group's requirement markers: all 34 apply on the supported CPython 3.13/Darwin arm64 environment; none apply to the tested Linux, Intel macOS, Python 3.11 or PyPy marker environments. These are marker evaluations, not executions on those platforms. The group-specific Python range is `>=3.13,<3.14`; project/core remains `>=3.11`.
- All three configuration hashes in `docs/evidence/M0/mlx-lock-audit.json` match the reviewed files, and those files match the exact review head.

These narrow read-only checks exited 0. No repeated 155-test suite was necessary for this configuration review.

## Installation evidence and platform boundary

`docs/evidence/M0/mlx-verification.json` records successful preflight on **CPython 3.13.7, macOS 26.3, arm64**, followed by the exact `--no-index --find-links ... --only-binary :all: --require-hashes` installation. Its receipt reports 34 resolved packages, 31 installed and three already satisfied. `uv pip check` reports all 42 installed packages compatible. Native imports report the pinned MLX/MLX-Metal/MLX-LM/Transformers/NumPy versions and Metal availability under offline environment settings. The existing test receipt records **155 passed**, exit 0. This is real dependency installation/loading evidence; it is not a model load, generation, feature qualification or optimizer experiment.

The lock-resolution receipt uses `--no-build`, and the installation uses only hash-approved wheels. The universal uv lock includes upstream source-distribution metadata, but the executed and documented approved installation path forbids selecting a source build. Git tag/tree identifiers in the report are identified as source provenance, not proof that the installed published wheels were locally built from those trees.

The frozen group dry run initially proposed removing the excluded editable project package. The retained correction in `mlx-final-check.json` adds `--inexact` and reports no changes. The first command was only a dry run; the documented command reflects the correction.

The native MLX and MLX-Metal wheel tags require **macOS 26+ arm64**. Requirement markers alone do not test the macOS release and can skip every optional package on other platforms; a successful filtered sync therefore cannot establish backend availability. `docs/reports/M0-mlx-dependencies.md` explicitly requires caller preflight before installation/use and assigns executable refusal to M2. Its preflight checks implementation, Python minor version, OS, architecture and macOS major version. This is an appropriate documented boundary for the present dependency-only slice, not a claim that M2's runtime guard already exists. Core portability remains unchanged in configuration; no Linux execution is asserted.

## Handoff obligations

M2 may consume this approved dependency slice. Before selecting the local backend, its implementation must enforce the documented platform/Metal checks and refuse unsupported platforms without CPU/CUDA/version/backend fallback. Actual pinned-model loading, inference, resource controls, output validation and generation evidence remain M2 work. Future Linux RL dependencies are a separate qualification and are not constrained or approved by this authoring group.

No M0 correction is requested. The scoped review package and original acquisition, installation and test receipts remain retained.
