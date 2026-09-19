"""Policy barrier diagnostics: identities are checked, no worker execution is faked."""
import pytest


def test_policy_barrier_requires_every_worker_and_fixed_probe():
    from feature_rl.training.state import PolicyStamp, PolicyBarrier
    stamp = PolicyStamp('p1', 'a'*64, 'b'*64, 'c'*64)
    barrier = PolicyBarrier(('worker-a', 'worker-b'))
    barrier.begin(stamp, (1, 2, 3))
    with pytest.raises(ValueError): barrier.require(stamp)
    barrier.acknowledge('worker-a', stamp, (1, 2, 3))
    with pytest.raises(ValueError): barrier.require(stamp)
    with pytest.raises(ValueError): barrier.acknowledge('worker-b', stamp, (1, 2, 4))
    barrier.acknowledge('worker-b', stamp, (1, 2, 3))
    barrier.require(stamp)
    state = barrier.state_dict()
    restored = PolicyBarrier(('worker-a', 'worker-b'))
    restored.load_state_dict(state)
    # Resume restores identity but cannot restore liveness/worker acknowledgment.
    with pytest.raises(ValueError): restored.require(stamp)
    barrier.begin(PolicyStamp('p2', 'd'*64, 'b'*64, 'c'*64), (2, 3))
    with pytest.raises(ValueError): barrier.require(stamp)


def test_uninitialized_policy_barrier_cannot_be_acknowledged():
    from feature_rl.training.state import PolicyBarrier
    barrier = PolicyBarrier(('worker',))
    with pytest.raises(ValueError): barrier.acknowledge('worker', None, None)
    with pytest.raises(ValueError): barrier.require(None)
