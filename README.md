# Feature RL

This project constructs immutable feature tasks from retained source evidence,
grades source implementations in a pinned Docker boundary, and checks current
automated qualification before release, agent execution or learning. It includes native
agent runs, GRPO training integration, frozen evaluation and historical audits.
Evaluation compares the base model (`base`) with feature-environment GRPO (`feature_grpo`).

Install the package with Python 3.11 or newer using `python -m pip install .` in
the intended environment. In this workspace, use the existing environment:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --help
PYTHONPATH=src .venv/bin/python -m feature_rl config-schema
```

The CLI exposes `construct`, `qualify`, `release`, `run`, `grade`, `audit`, `train`
and `evaluate`, plus source screening, authoring, retained-history import,
resolution and publication recovery. Commands use strict JSON inputs
and the actual library services. See [the runbook](docs/runbook.md) for commands,
configuration and recovery, and [the native launch contract](docs/reports/M7-native-launch.md)
for pinned Linux/CUDA setup, training and checkpoint resume.

Environment authoring uses local Transformers/PyTorch with a configured model,
immutable manifests, and an explicit CPU or CUDA device. It requires a prepared
local safetensors model and matching pinned dependencies; see
[the authoring setup](docs/interfaces-M2.md). The historical Mac/MLX configuration
is no longer the active backend. This replacement has not been exercised with a model.

The labeled Click fixture executed 13 real grades and 39 cases through M3–M6. It
remains historical diagnostic evidence and does not replace the original exhausted
feature-generation attempt or establish a newly qualified real task. See
[the retained fixture report](docs/reports/M6-click-fixture.md).

[Historical CPU/CLI verification](docs/evidence/integration/final/receipt.json) and
[progress](docs/progress.md) distinguish earlier executed checks from the unexecuted cleanup.
The Docker runtime requires an explicit repository profile and pinned base image,
and a required [construction-time cached runtime image](docs/interfaces-M3.md).
Legacy Click policies and recipes without prebuilt images are no longer accepted.
New construction requires a publishing registry and complete pinned inputs.
[`construct-feature --github`](docs/runbook.md) captures a selected PR/issue and Git
history and passes the frozen inputs directly to construction. Existing captures
can be selected with `--prepared`; private repositories support token authentication.
Mixed-file labels are resolved through permitted-source projection and automated
qualification. Runtime dependencies and experiment splits remain explicit.
GPU training,
native GPU inference, new task generation and experimental results remain
deferred and unverified. No real task has been fully qualified and released.
SSH human approval is not required for new task admission.
