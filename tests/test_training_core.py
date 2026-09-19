"""CPU/unit diagnostics only; no released task or learning result is asserted."""
import importlib.util
import pytest


def test_training_core_is_implemented():
    assert importlib.util.find_spec('feature_rl.training.core') is not None


def test_valid_only_groups_do_not_reward_singletons_or_drop_uniform_costs():
    from feature_rl.training.core import group_advantages
    assert group_advantages((1, 0, None, 0)) == (2/3, -1/3, None, -1/3)
    assert group_advantages((1, None, None, None)) == (0., None, None, None)
    assert group_advantages((1, 1, 1, 1)) == (0., 0., 0., 0.)
    assert group_advantages((None, None, None, None)) == (None,)*4
    with pytest.raises(ValueError):
        group_advantages((1, 0))
    with pytest.raises(ValueError):
        group_advantages((True, 0, 0, 0))


def test_sampler_is_balanced_seeded_and_resume_exact():
    from feature_rl.training.core import BalancedSampler, TaskSlot
    slots = [TaskSlot('a', 'f1', 'x'), TaskSlot('b', 'f1', 'x'), TaskSlot('c', 'f2', 'y')]
    sampler = BalancedSampler(slots, seed=11)
    first = [sampler.next_group('p1') for _ in range(4)]
    assert [g.task.family for g in first].count('f1') == 2
    assert all(len(set(g.episode_seeds)) == 4 for g in first)
    state = sampler.state_dict()
    expected = sampler.next_group('p2')
    resumed = BalancedSampler(slots, seed=11)
    resumed.load_state_dict(state)
    assert resumed.next_group('p2') == expected
    with pytest.raises(ValueError):
        BalancedSampler(slots[:-1], seed=11).load_state_dict(state)


def test_signal_gate_counts_once_and_stops_at_bounded_probe():
    from feature_rl.training.core import SignalGate
    gate = SignalGate()
    for n in range(8):
        gate.observe(str(n), str(n % 4), (1, 0, 0, 0))
    assert gate.ready
    with pytest.raises(ValueError):
        gate.observe('0', '0', (1, 0, 0, 0))
    empty = SignalGate()
    for n in range(64):
        empty.observe(str(n), 'task', (0, 0, 0, 0))
    with pytest.raises(ValueError):
        empty.observe('65', 'task', (0, 0, 0, 0))
    assert not empty.ready


def test_exact_context_and_policy_are_validated_without_prefix_flattening():
    from feature_rl.contracts import TokenTrace
    from feature_rl.training.core import validate_trace
    trace = TokenTrace(context_token_ids=(1, 2), sampled_token_ids=(3, 4),
                       behavior_log_probabilities=(-1., -2.), assistant_loss_mask=(True, True), policy_version='p1')
    validate_trace(trace, context=(1, 2), policy_version='p1', vocab_size=8, max_seq_len=4)
    for kwargs in [dict(context=(1, 5)), dict(policy_version='p0'), dict(max_seq_len=3), dict(vocab_size=4)]:
        inputs=dict(context=(1, 2), policy_version='p1', vocab_size=8, max_seq_len=4) | kwargs
        with pytest.raises(ValueError): validate_trace(trace, **inputs)
