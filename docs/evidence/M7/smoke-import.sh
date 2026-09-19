#!/usr/bin/env bash
# Deferred import/device gate only; not run during source investigation.
# CWD must be the complete pinned SkyRL checkout with preinstalled frozen extras.
set -euo pipefail
[[ "$(git rev-parse HEAD)" == f5bc3b78dfddfb352870d5d7430cd226e5785838 ]]
[[ "$(uname -s)" == Linux ]]
[[ "$(uname -m)" == x86_64 ]]
uv run --offline --frozen --no-sync --extra fsdp --extra harbor python - <<'PY'
import importlib.metadata as metadata
import json
import sys
import torch
import vllm
from harbor.models.agent.rollout_detail import RolloutDetail
from harbor.models.trial.config import TrialConfig
from harbor.trial.trial import Trial
from skyrl.train.generators.base import GeneratorInput, GeneratorOutput, TrajectoryID
from skyrl.train.trainer import RayPPOTrainer
from examples.train_integrations.harbor.harbor_generator import HarborGenerator
assert sys.version_info[:2] == (3, 12)
assert metadata.version('harbor') == '0.13.1'
origin = json.loads(metadata.distribution('harbor').read_text('direct_url.json') or '{}')
assert origin.get('vcs_info', {}).get('commit_id') == '3de07a0e01f3368921766437fc7afece3ddec23d', 'Missing/mismatched Harbor origin; inspect pinned install metadata'
assert torch.__version__.split('+')[0] == '2.11.0'
assert metadata.version('vllm').split('+')[0] == '0.23.0'
assert torch.cuda.is_available(), 'CUDA unavailable'
x = torch.zeros(1, device='cuda')
torch.cuda.synchronize()
print(json.dumps({'gate': 'imports_and_cuda_allocation_only', 'torch': torch.__version__, 'torch_cuda': torch.version.cuda, 'device': torch.cuda.get_device_name(0), 'vllm': metadata.version('vllm'), 'harbor': metadata.version('harbor')}))
PY
