"""Real Torch CPU diagnostics. Synthetic tokens/models are never feature-learning evidence."""
import copy
import importlib.util
import pytest


def test_torch_backend_exists():
    assert importlib.util.find_spec('feature_rl.training.torch_backend') is not None


@pytest.fixture
def torch():
    return pytest.importorskip('torch')


@pytest.fixture
def model(torch):
    class TinyCausalModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Embedding(9, 6)
            self.output = torch.nn.Linear(6, 9)
        def forward(self, input_ids, attention_mask=None):
            return type('Output', (), {'logits': self.output(self.embed(input_ids))})()
    torch.manual_seed(13)
    return TinyCausalModel()


def test_assistant_mask_removes_harness_tokens_from_policy_and_sft_loss(torch):
    from feature_rl.training.torch_backend import clipped_surrogate, supervised_loss
    logits = torch.tensor([-.5, -.7, -.9], requires_grad=True)
    mask = torch.tensor([True, False, True])
    loss = supervised_loss(logits, mask)
    loss.backward()
    assert logits.grad.tolist() == [-.5, 0., -.5]
    logits.grad = None
    loss = clipped_surrogate(logits, logits.detach(), torch.ones(3), mask, .2, .2)
    loss.backward()
    assert logits.grad.tolist() == [-.5, 0., -.5]


def test_sft_updates_real_parameters_and_frozen_reference(torch, model):
    from feature_rl.training.torch_backend import CausalTurn, TorchUpdater, model_digest
    updater = TorchUpdater(model, learning_rate=.01, kl_coefficient=0.)
    reference_before = model_digest(updater.reference)
    turn = CausalTurn((1, 2), (3, 4), (True, True))
    receipt = updater.update([turn], algorithm='sft')
    assert receipt.updated and receipt.gradient_norm > 0
    assert receipt.before != receipt.after
    assert model_digest(updater.reference) == reference_before
    assert updater.optimizer_steps == 1
    assert updater.optimizer.state_dict()['state']


def test_grpo_changes_weights_using_exact_behavior_logprobs(torch, model):
    from feature_rl.training.torch_backend import CausalTurn, TorchUpdater
    updater = TorchUpdater(model, learning_rate=.01, kl_coefficient=0.)
    turns = []
    for target, advantage in [(3, .5), (4, -.5)]:
        raw = CausalTurn((1, 2), (target,), (True,))
        behavior = tuple(updater.logprobs(raw).detach().tolist())
        turns.append(CausalTurn(raw.context, raw.targets, raw.mask, behavior, advantage))
    result = updater.update(turns, algorithm='grpo')
    assert result.updated and result.gradient_norm > 0 and result.before != result.after
    with pytest.raises(ValueError): updater.update([raw], algorithm='grpo')


def test_zero_gradient_skips_optimizer_weight_decay(torch, model):
    from feature_rl.training.torch_backend import CausalTurn, TorchUpdater
    updater = TorchUpdater(model, learning_rate=.01, kl_coefficient=0., weight_decay=.1)
    raw = CausalTurn((1, 2), (3,), (True,))
    turn = CausalTurn(raw.context, raw.targets, raw.mask, tuple(updater.logprobs(raw).detach().tolist()), 0.)
    result = updater.update([turn], algorithm='grpo')
    assert not result.updated and result.before == result.after and updater.optimizer_steps == 0


def test_invalid_or_nonfinite_turn_cannot_partially_update(torch, model):
    from feature_rl.training.torch_backend import CausalTurn, TorchUpdater, model_digest
    updater = TorchUpdater(model, learning_rate=.01)
    before = model_digest(model)
    for turn in [CausalTurn((1,), (3,), (False,)), CausalTurn((1,), (99,), (True,))]:
        with pytest.raises(ValueError): updater.update([turn], algorithm='sft')
        assert model_digest(model) == before


def test_checkpoint_resume_preserves_optimizer_rng_and_next_update(torch, model, tmp_path):
    from feature_rl.training.torch_backend import CausalTurn, TorchUpdater, model_digest
    updater = TorchUpdater(model, learning_rate=.01, kl_coefficient=0.)
    turn = CausalTurn((1, 2), (3, 4), (True, True))
    updater.update([turn], algorithm='sft')
    binding = {'configuration': 'a'*64, 'tasks': ['b'*64], 'reference': 'c'*64,
               'tokenizer': 'd'*64, 'template': 'e'*64}
    checkpoint = updater.save_checkpoint(tmp_path / 'checkpoint', binding=binding,
                                         progress={'sampler': {'position': 1}, 'signal': {}}, policy_version='p1')
    expected_random = torch.rand(4)
    expected_receipt = updater.update([turn], algorithm='sft')
    restored = TorchUpdater(copy.deepcopy(model), learning_rate=.01, kl_coefficient=0.)
    state = restored.load_checkpoint(tmp_path / 'checkpoint', expected_digest=checkpoint, binding=binding)
    assert state['progress']['sampler']['position'] == 1 and state['policy_version'] == 'p1'
    assert torch.equal(expected_random, torch.rand(4))
    actual_receipt = restored.update([turn], algorithm='sft')
    assert actual_receipt.after == expected_receipt.after
    assert restored.optimizer_steps == updater.optimizer_steps == 2
    with pytest.raises(ValueError):
        restored.load_checkpoint(tmp_path / 'checkpoint', expected_digest=checkpoint, binding=binding | {'tasks': []})
    weight_file = tmp_path / 'checkpoint' / 'state.pt'
    weight_file.write_bytes(weight_file.read_bytes() + b'corrupt')
    with pytest.raises(ValueError):
        restored.load_checkpoint(tmp_path / 'checkpoint', expected_digest=checkpoint, binding=binding)
