"""Actual CPU tensor functions and source-contract checks; no SkyRL runtime claim."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_bridge_module_exists():
    import importlib.util
    assert importlib.util.find_spec('feature_rl.training.skyrl_bridge') is not None


def test_framework_grpo_uses_behavior_denominator_and_upstream_sum_reduction():
    torch = pytest.importorskip('torch')
    from feature_rl.training.skyrl_bridge import feature_grpo_loss, feature_sft_loss
    current = torch.tensor([[-.5, -.5]], requires_grad=True)
    config = SimpleNamespace(eps_clip_low=.2, eps_clip_high=.2)
    # Recomputed old=-10 would clip differently; sampled behavior exactly equals current.
    loss, _ = feature_grpo_loss(current, torch.full_like(current, -10.), torch.tensor([[.5, .5]]), config,
        torch.tensor([[1., 0.]]), current.detach())
    loss.backward()
    assert current.grad.tolist() == [[-.5, 0.]]
    current.grad = None
    loss, _ = feature_sft_loss(current, None, torch.tensor([[.25, .25]]), config, torch.tensor([[1., 0.]]))
    loss.backward()
    assert current.grad.tolist() == [[-.25, 0.]]


def test_bridge_calls_are_actual_pinned_trainer_symbols():
    from feature_rl.training.skyrl_bridge import PINNED_SKYRL, PINNED_HARBOR
    assert PINNED_SKYRL == 'f5bc3b78dfddfb352870d5d7430cd226e5785838'
    assert PINNED_HARBOR == '3de07a0e01f3368921766437fc7afece3ddec23d'
    root = Path('.feature-rl/research/M7/skyrl/skyrl/train/trainer.py')
    if not root.exists(): pytest.skip('Pinned private source audit not present')
    tree = ast.parse(root.read_text())
    methods = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {'convert_to_training_input','fwd_logprobs_values_reward','train_critic_and_policy',
            'init_weight_sync_state','save_checkpoints','load_checkpoints'} <= methods
