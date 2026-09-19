# M3 Click dependency acquisition preparation

Prepared 2026-09-19 UTC. **Acquisition and static metadata validation only.** Six inert wheels are available: the five exact locked Python 3.12/Linux test dependencies, plus an explicitly chosen neutral build-backend pin. No acquired package was imported, no package was installed, no build hook or historical source was executed, and no Docker container was created. No Git command, current `GitHistory` API, product code edit, or user configuration change was used in this preparation.

## Baseline integrity and bounded reading

Read `.feature-rl/research/M1/git-archives/click-B-19fd4d6e18bc9fce451f92f422696b11169faa57.tar` only after verifying its 1,546,240-byte file against the SHA-256 archived by M1: `9de5108a0e639b8e502117d4fbe955cb2dd2cb38319fc853fde3c222c16eb595`. The tar's global comment identifies B `19fd4d6e18bc9fce451f92f422696b11169faa57`; its manifest-member timestamp is 2026-04-29T23:37:31Z. This timestamp is archive evidence, not a newly independently queried Git fact.

The reader bounds the archive at 32 MiB and each manifest member at 2 MiB, checks that each requested member occurs exactly once and is a regular file, and reads it as data without filesystem extraction. It reads only:

| Member | Bytes | SHA-256 |
| --- | ---: | --- |
| `pyproject.toml` | 5,372 | `f99bdc45ed564e9f228bfe62c8a0393d505f8627211c216b83cba93fd6fa5254` |
| `.github/workflows/tests.yaml` | 2,191 | `3edc6420fb4ae1a6d49f6a29956fa45e7e4bc04540354598d432e6f0a6b7b2b6` |
| `uv.lock` | 258,593 | `e16f03080103c0ab9d80185d78a3a97f15fe52de5410617e2cc0fb32558cf446` |

B requires Python >=3.10, its CI includes Python 3.12, and its `tests` dependency group contains pytest. The tox configuration requests a wheel build and frozen constraints. The selected target is the previously probed CPython **3.12.14 / Linux aarch64** image `python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333`; see [sandbox investigation](investigation.md). No new image operation was needed.

## Exact acquired wheel inventory

All six wheel filenames end in `-py3-none-any.whl`; their internal WHEEL metadata declares `Root-Is-Purelib: true` and exactly `Tag: py3-none-any`. Thus the artifacts are architecture-independent and applicable to the chosen Python 3.12 interpreter. Every selected release is currently unyanked. The five test-wheel URL, byte-size, SHA-256, and version values match B's lockfile **and** release-specific official PyPI metadata; downloaded bytes match all those values. The backend is separately labeled below.

| Wheel | Bytes | Python requirement | Source of version/hash |
| --- | ---: | --- | --- |
| `pytest-9.0.2-py3-none-any.whl` | 374,801 | >=3.10 | B lock |
| `iniconfig-2.3.0-py3-none-any.whl` | 7,484 | >=3.10 | B lock |
| `packaging-26.0-py3-none-any.whl` | 74,366 | >=3.8 | B lock |
| `pluggy-1.6.0-py3-none-any.whl` | 20,538 | >=3.9 | B lock |
| `pygments-2.20.0-py3-none-any.whl` | 1,231,151 | >=3.9 | B lock |
| `flit_core-3.11.0-py3-none-any.whl` | 44,926 | >=3.6 | Proposed neutral reconstruction; official PyPI |

```text
pytest==9.0.2 --hash=sha256:711ffd45bf766d5264d487b917733b453d917afd2b0ad65223959f59089f875b
iniconfig==2.3.0 --hash=sha256:f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12
packaging==26.0 --hash=sha256:b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529
pluggy==1.6.0 --hash=sha256:e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746
pygments==2.20.0 --hash=sha256:81a9e26dd42fd28a23a2d169d86d7ac03b46e2f8b59ed4698fb4785f946d0176
flit-core==3.11.0 --hash=sha256:fe464c086f630f106c0fc5001ee377980f45938f03f8f0d03da08a4841748541
```

Full URLs, exact release/retrieval times, lock records, internal metadata, and hashes are in [inventory.json](dependency-metadata/inventory.json). The [proposed requirements file](dependency-metadata/proposed-requirements.txt) is ready for a future offline installation. **Wheel bytes remain private under `.feature-rl/research/M3/dependencies/`** and are not checked into evidence or solver-visible exports. No Click wheel from PyPI was acquired.

## Closed dependency graph

The baseline and wheel metadata agree for this target:

- Click's only declared third-party runtime requirement is `colorama` on Windows; it is inactive on Linux.
- pytest 9.0.2 requires active `iniconfig>=1.0.1`, `packaging>=22`, `pluggy>=1.5,<2`, and `pygments>=2.7.2`. All four locked versions satisfy their constraints and have no active further dependencies.
- pytest's `colorama` Windows marker and `exceptiongroup`/`tomli` Python <3.11 markers are false here. Their wheels are unnecessary and were not fetched.
- No package extras are requested. pytest/pluggy development/testing extras and Pygments' `windows-terminal` extra are therefore inactive.
- flit_core 3.11.0 declares no external runtime dependency. Its wheel includes vendored tomli 1.2.3 metadata; this is already covered by the backend wheel hash and is not a request to install a separate tomli distribution.

The metadata checker used the **already installed trusted host packaging 26.3 parser** solely to evaluate inert version constraints and markers under an explicit target environment. It did not choose dependency versions, import a fetched wheel, or run host pytest. The selected runtime packaging remains the historical **26.0** artifact. [Verification output and every marker decision](dependency-metadata/verification.json) identify the parser origin/version. All `Requires-Python` constraints, active `Requires-Dist` constraints, lock markers, filenames, and downloaded hashes passed static checks. Official [PyPI release metadata](https://docs.pypi.org/api/json/) and the [core metadata specification](https://packaging.python.org/en/latest/specifications/core-metadata/) document the fields used.

## Explicit reconstructed build pin

B declares `flit_core>=3.11,<4`, backend `flit_core.buildapi`, but **uv.lock contains no flit-core record**. The exact backend installed by historical CI cannot be recovered from those inputs.

The coordinator authorized an explicit neutral selection rather than treating this ordinary reconstruction gap as a stop. Selection rule: choose the exact stable lower-bound release **3.11.0**, verify that it satisfies B's range and Python 3.12, has an available universal wheel without external dependencies, and predates the baseline archive timestamp. Official [flit_core 3.11.0 release metadata](https://pypi.org/pypi/flit_core/3.11.0/json) reports its wheel upload at **2025-02-19T08:22:14.611170Z**, before the archive's 2026-04-29 timestamp. The 44,926-byte wheel's SHA-256 is `fe464c086f630f106c0fc5001ee377980f45938f03f8f0d03da08a4841748541`.

This is a **proposed neutral reconstruction pin, not a recovered historical pin**. Successful build and behavior compatibility remain unexecuted. If the pin fails later, record the failure and any explicit replacement as a recipe repair, apply it equally to B/H/candidates, and revalidate; never substitute latest silently.

## Proposed neutral installation/build policy — not executed

Use the existing hardened Docker configuration and exactly the pinned interpreter/image, with identical locale, timezone, hash seed, build inputs, and environment variables across B/H/candidates. Stage only the six approved wheels and hash requirements into a read-only dependency supply. No runtime network, source distribution, automatic dependency resolution, or package download is permitted.

1. In a disposable hardened construction worker, use the image's bundled pip (its bytes are fixed by the image digest; its reported version must still be recorded) to install these exact wheels into a dedicated dependency directory with `--isolated --no-index --find-links /supply --require-hashes --no-deps --only-binary=:all: --no-compile --ignore-installed --target /workspace/deps -r /supply/proposed-requirements.txt`. Those are proposed pip-install arguments, not an executed command. Validate actual imported test/backend paths. No H/source code is present in this reusable dependency step.
2. For each B, H, or submitted candidate separately, start fresh with its validated source snapshot and the same immutable dependency supply. Build its own Click wheel with the pinned backend, offline, using pip's `wheel --no-build-isolation --no-deps --no-index --wheel-dir /workspace/built /workspace/source` and the dedicated dependency directory on the worker's Python import path. `--no-build-isolation` prevents an implicit second build-dependency resolution. This preserves B's declared wheel-build approach while avoiding the larger uv/tox orchestration stack. The build runs only inside the untrusted worker; the controller never imports it. pip documents these [wheel-build controls](https://pip.pypa.io/en/stable/cli/pip_wheel/).
3. Install only that just-built, recorded Click wheel into a fresh snapshot-specific target directory, offline with no dependency resolution. Verify `click.__file__`, distribution metadata, wheel hash, and source-snapshot binding so an image-installed or future Click cannot shadow the intended artifact. Keep B/H build products separate; never put H in a common layer. Do not reuse candidate caches or installed state.
4. Run B's public regression command semantics (`pytest -v --tb=short --basetemp=<bounded scratch>`) using the installed snapshot wheel and the exact locked test tools. Preserve B's existing `not stress` default and warnings policy. This is a minimal reconstruction of the test environment, **not a claim to run the full uv/tox CI wrapper**, typing, style, docs, randomization, or stress groups. Record any required, behavior-neutral packaging-path adjustment equally for all snapshots; do not change assertions or application semantics.
5. Complete actual sandbox/lifecycle and source-artifact validation, baseline health, imported-path evidence, B/H compatibility, fresh-run/reset repetitions, and cleanup gates after coordinator handoff. tmpfs `noexec`, the initial 128 MiB resource settings, pip availability, and the direct wheel workflow must be tested for compatibility. No such execution is claimed here.

## Commands, failures, receipts, and cost

Trusted acquisition scripts use only bounded HTTP, hashing, archive-member reads, ZIP metadata reads, and serialization; they execute no downloaded bytecode or hooks. The executed successful driver commands were:

```sh
python3 -I .feature-rl/research/M3/dependencies/acquire.py
python3 -I .feature-rl/research/M3/dependencies/acquire-backend.py
.venv/bin/python -I .feature-rl/research/M3/dependencies/verify-inventory.py
```

Each successful command exited 0. Output: `pass_static_integrity_and_dependency_closure_only wheels 6 bytes 1753266`. [Acquisition source/log](dependency-metadata/acquire.py), [backend source/log](dependency-metadata/acquire-backend.py), and [verification source](dependency-metadata/verify-inventory.py) are preserved. Initial backend metadata was fetched from the exact version URL above using bounded stdlib HTTPS; [its response receipt](dependency-metadata/backend-metadata-receipt.json) retains URL, headers, UTC, status, size, and body hash.

The first backend validation exited 1 because its generic metadata suffix match also found vendored tomli's METADATA. The observer was corrected to select exactly `flit_core-3.11.0.dist-info/METADATA`; no artifact, dependency, or policy was changed. The original [failed source](dependency-metadata/attempt1-acquire-backend.py) and [failure output](dependency-metadata/attempt1-acquire-backend.log) are retained. The backend wheel was downloaded a second time; the first failed observer had not yet serialized its HTTP receipt, so only the successful retry has full headers/UTC. Its byte count is included below rather than concealed.

[HTTP receipts](dependency-metadata/http-receipts.json) cover all five locked JSON responses and wheel downloads; [backend wheel receipt](dependency-metadata/backend-wheel-receipt.json) covers the successful sixth wheel. All recorded HTTP statuses are 200. Unique acquisition: **12 artifacts (6 JSON + 6 wheels), 1,788,696 bytes**, of which **1,753,266 bytes** are wheels. Including the repeated 44,926-byte backend download: **13 HTTP acquisition requests, 1,833,622 response-body bytes**. Both counts are within the authorized 20-artifact / 30-MiB limits. No wheel was unavailable, no sdist was fetched, and no latest-version fallback occurred. No paid service or remote compute was used; monetary, token, and human-review costs remain **unknown**, not entered as zero.
