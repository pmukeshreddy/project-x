# M0 — optional local MLX authoring dependencies

Status: dependency/configuration slice implemented and executed; independent review pending. Base `3761b3f`. No M2 product, schemas, model assets or root progress/review files were edited. No inference, model download, package source build or global/user configuration change was performed.

## Configuration and provenance

`pyproject.toml` adds the opt-in `authoring-mlx` dependency group, pinning all 34 packages from M2's successfully installed closure. It is not in the default groups. Each requirement is guarded by Darwin/arm64/CPython/Python-3.13 markers, and the group has its own `requires-python = ">=3.13,<3.14"`. The project's `requires-python = ">=3.11"`, core dependencies and existing dev versions remain unchanged. Group-specific Python constraints are supported by [uv's dependency-group configuration](https://docs.astral.sh/uv/concepts/projects/dependencies/#group-requires-python).

`uv.lock` freezes versions and distribution hashes for the optional closure. `requirements-authoring-mlx.lock` additionally pins exactly one approved wheel hash per package for the tested platform; it supports bounded offline installation from the verified M2 wheelhouse. All 34 frozen versions and approved wheel hashes match the production uv lock. The existing 12 core/dev/root locked package versions are unchanged (`docs/evidence/M0/mlx-lock-audit.json`). This configuration does not constrain a future Linux training stack to MLX or install MLX in the default core environment.

The local `dependency-lock.json`, `requirements-lock.txt`, install/freeze logs and model-acquisition manifest under `.feature-rl/research/M2` were read as supplied evidence. Every one of the 34 local wheels was independently checked against the recorded SHA-256 and size before installation: **96,889,626 bytes** total. Metadata was read as inert ZIP data, including exact Requires-Python, Requires-Dist and wheel tags; see `docs/evidence/M0/mlx-wheel-metadata.json`.

| Package | Version | Verified wheel tag | SHA-256 |
| --- | --- | --- | --- |
| mlx | 0.32.2 | cp313-cp313-macosx_26_0_arm64 | df8c75e509de868fca148dfeb38d92ce956eed386569c87caeb72bd16d2d6962 |
| mlx-metal | 0.32.2 | py3-none-macosx_26_0_arm64 | e6abeac9ac5265830c9c1541b6f96e9be37a85c2446763a46ad466c63a3837ab |
| mlx-lm | 0.31.3 | py3-none-any | 758cfddf1180053b7613db76fad3d246a331a2a905808e1164a275621fc983b8 |

Recorded source tags agree with the archived GitHub metadata: [MLX v0.32.2](https://github.com/ml-explore/mlx/tree/1f8e74e3f12f31365464a6867c6579f0e9b29d85) and [MLX-LM v0.31.3](https://github.com/ml-explore/mlx-lm/tree/ed1fca4cef15a824c5f1702c80f70b4cffc8e4dd). These are source-provenance identifiers; the installed artifacts are verified published wheels, not local builds of those trees.

## Supported selection and explicit refusal

This authoring backend is qualified here only on **CPython 3.13.7, macOS 26.3, Apple Silicon arm64**. Its dependency group permits the CPython 3.13 ABI; the native wheel tags require macOS 26 or newer. This is narrower than individual package metadata: mlx/mlx-metal declare Python >=3.10, mlx-lm >=3.8, while this frozen NumPy declares >=3.12. Those declarations do not establish a tested closure for older interpreters or other platforms.

**Reject selection on Linux, Intel macOS, macOS <26, non-CPython, or a Python minor version other than 3.13. Do not silently choose CPU, CUDA, another package version, or another backend.** On excluded platforms dependency markers can skip packages; a successful marker-filtered sync is not authoring-backend availability. The selected backend's caller must run the following explicit preflight before installation/use, and M2's actual runtime must retain that refusal behavior. The default core remains available on its declared platforms; no Linux runtime was tested in this slice.

```sh
.venv/bin/python - <<'PY'
import platform, sys
supported = (
    sys.implementation.name == "cpython"
    and sys.version_info[:2] == (3, 13)
    and platform.system() == "Darwin"
    and platform.machine() == "arm64"
    and int(platform.mac_ver()[0].split(".")[0]) >= 26
)
if not supported:
    raise SystemExit("Unsupported authoring-mlx platform: require CPython 3.13, macOS 26+ arm64")
PY
```

## Frozen wheel-only installation

After successful preflight, reuse the already verified wheelhouse:

```sh
uv pip install --python .venv/bin/python --no-index \
  --find-links .feature-rl/research/M2/downloads \
  --only-binary :all: --require-hashes -r requirements-authoring-mlx.lock
uv pip check --python .venv/bin/python
```

This exact install was executed. It uses no network/index, forbids source builds, and verifies the platform-specific wheel hashes. Do not replace it with an unconstrained install or source fallback. If a fresh machine lacks the verified wheelhouse, acquire only the frozen wheels under the acquisition budget/evidence policy before using this command.

To maintain the already installed group against the production lock without rebuilding this project's editable package:

```sh
uv sync --frozen --group authoring-mlx --no-install-project \
  --inexact --no-build --offline --dry-run
```

The final dry run reports no changes. `--inexact` preserves the existing project installation: the earlier dry run without it would remove that excluded project package, so that earlier command was not applied. Use the documented flags. Core source commands may still require `PYTHONPATH=src` because of this host's separately recorded hidden editable `.pth` behavior.

## Executed verification and limits

`docs/evidence/M0/mlx-lock-resolution.json` records `uv lock --no-build --python 3.13.7`: **43 packages resolved**, exit 0. All optional closure versions were explicitly pinned before resolution; existing core pins were retained. No source build was allowed.

`docs/evidence/M0/mlx-verification.json` contains exact argv, UTC timestamps, revision, environment overrides, stdout/stderr and statuses:

- Platform preflight: macOS 26.3 arm64 and CPython 3.13.7, exit 0.
- Offline hash-required binary-only installation: 34 packages resolved, 31 installed, 3 already satisfied; exit 0.
- `uv pip check --python .venv/bin/python`: all 42 installed packages compatible, exit 0.
- Native imports: mlx.core, mlx_lm, transformers and numpy; versions mlx 0.32.2, mlx-metal 0.32.2, mlx-lm 0.31.3, transformers 5.17.0 and numpy 2.5.3; Metal available=True, exit 0. HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 were set for verification; no model was loaded.
- `.venv/bin/python -m pytest -q`: **155 passed**, exit 0, run once after installation. No new tests were added for this configuration-only slice.

`mlx-lock-audit.json` binds the tested configuration files by digest. `mlx-final-check.json` records the corrected frozen dry-run command and scoped whitespace check. Native imports/Metal availability establish dependency loading only. M2 still owns actual local inference, model revision/resource controls, generation behavior and empirical qualification. This optional authoring group is not an RL training-stack qualification.
