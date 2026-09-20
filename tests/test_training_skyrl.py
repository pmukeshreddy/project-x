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
    from feature_rl.training.skyrl_bridge import feature_grpo_loss
    current = torch.tensor([[-.5, -.5]], requires_grad=True)
    config = SimpleNamespace(eps_clip_low=.2, eps_clip_high=.2)
    # Recomputed old=-10 would clip differently; sampled behavior exactly equals current.
    loss, _ = feature_grpo_loss(current, torch.full_like(current, -10.), torch.tensor([[.5, .5]]), config,
        torch.tensor([[1., 0.]]), current.detach())
    loss.backward()
    assert current.grad.tolist() == [[-.5, 0.]]


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


def pinned_conversion(monkeypatch):
    """Execute exact inert upstream tensor/conversion functions, no CUDA imports.

    Only the batch container, DP padding (size zero) and trainer resources are
    diagnostic doubles. Tensor construction and both boundary functions are native.
    """
    import hashlib, logging, math, sys, textwrap, types
    torch = pytest.importorskip('torch')
    root = Path('docs/evidence/M7/training-core-round1/source')
    namespace = {'torch':torch, 'logger':logging.getLogger(__name__), 'math':math}
    for name, expected in (
        ('preprocess.py.txt','9f7667890b9d789e6cc9875dca666627f493ceb3db55fc97599c244c6f25e205'),
        ('convert_to_training_input.py.txt','b3922d64c81080d99286fd5d1e6acaf41feadbca605eee2a129e14c18fc7a131'),
    ):
        source = (root/name).read_bytes()
        assert hashlib.sha256(source).hexdigest() == expected
        tree = ast.parse(textwrap.dedent(source.decode()))
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),*funcs],type_ignores=[])
        exec(compile(ast.fix_missing_locations(module),str(root/name),'exec'),namespace)
    class Batch(dict):
        @property
        def batch_size(self): return len(self['sequences'])
    def pad(batch, size):
        assert size == 0  # Distributed padding is outside this CPU contract diagnostic.
        return batch
    namespace.update(TrainingInputBatch=Batch,pad_training_input_batch=pad)
    upstream = types.ModuleType('skyrl.train.dataset.preprocess')
    for name in ('compute_prompt_boundaries','compute_prompt_mini_batch_boundaries'):
        setattr(upstream,name,namespace[name])
    monkeypatch.setitem(sys.modules,'skyrl.train.dataset.preprocess',upstream)
    class Trainer:
        convert_to_training_input = namespace['convert_to_training_input']
    trainer = Trainer()
    trainer.cfg = SimpleNamespace(trainer=SimpleNamespace(train_batch_size=4,policy_mini_batch_size=2,
        critic_mini_batch_size=2,critic=SimpleNamespace(model=SimpleNamespace(path='')),
        algorithm=SimpleNamespace(max_seq_len=8,off_policy_correction=SimpleNamespace(tis_ratio_type=None,sequence_mask_metric=None))),
        generator=SimpleNamespace(n_samples_per_prompt=4,step_wise_trajectories=True))
    trainer.tokenizer = SimpleNamespace(pad_token_id=0)
    trainer.dispatch = SimpleNamespace(get_lcm_dp_size=lambda:1)
    trainer.all_metrics = {}
    return trainer


@pytest.mark.parametrize('counts,mini,expected', [([2],1,[(0,2)]), ([3,2,4],2,[(0,5),(5,9)]),
    ([2,3],4,[(0,5)]), ([2,2,3,2],2,[(0,4),(4,9)])])
def test_native_conversion_consumes_eligible_groups_and_final_remainder(monkeypatch,counts,mini,expected):
    from feature_rl.training.skyrl_bridge import convert_eligible_batch
    trainer = pinned_conversion(monkeypatch)
    original = trainer.cfg
    original.trainer.policy_mini_batch_size = mini
    uids = [str(group) for group,count in enumerate(counts) for _ in range(count)]
    output = dict(prompt_token_ids=[[1,2]]*len(uids),response_ids=[[3]]*len(uids),
        rewards=[[float(i%2)] for i in range(len(uids))],loss_masks=[[1]]*len(uids),
        rollout_logprobs=[[-1.]]*len(uids),is_last_step=[True]*len(uids))
    # Reproduce the old assertion for a filtered/nondivisible batch before exercising the adapter.
    if len(counts) != 4 or 4 % mini:
        with pytest.raises(AssertionError): trainer.convert_to_training_input(output,uids)
    data = convert_eligible_batch(trainer,output,uids)
    assert data.metadata['policy_mini_batch_boundaries'] == expected
    assert data.metadata['critic_mini_batch_boundaries'] == [
        (sum(counts[:i]),sum(counts[:min(i+2,len(counts))])) for i in range(0,len(counts),2)]
    assert data.metadata['policy_prompt_boundaries'] == [(sum(counts[:i]),sum(counts[:i+1])) for i in range(len(counts))]
    assert data.metadata['uids'] == uids
    assert data['rewards'].tolist() == output['rewards']
    assert data['rollout_logprobs'].tolist() == output['rollout_logprobs']
    assert data['loss_mask'].tolist() == output['loss_masks']
    assert len(data['sequences']) == len(uids)
    assert trainer.cfg is original and original.trainer.train_batch_size == 4
    assert original.trainer.policy_mini_batch_size == mini


def test_conversion_rejects_noncontiguous_groups_without_changing_config(monkeypatch):
    from feature_rl.training.skyrl_bridge import convert_eligible_batch
    trainer = pinned_conversion(monkeypatch)
    original = trainer.cfg
    with pytest.raises(ValueError,match='contiguous'):
        convert_eligible_batch(trainer,{},['a','b','a'])
    with pytest.raises(KeyError):
        convert_eligible_batch(trainer,{},['a'])
    assert trainer.cfg is original and original.trainer.train_batch_size == 4


def test_singleton_rows_are_omitted_without_replacing_assigned_records_or_costs():
    from feature_rl.training.core import GroupPlan, TaskSlot, group_advantages
    from feature_rl.training.data import PreparedGroup
    from feature_rl.training.skyrl_bridge import group_rows
    from feature_rl.training.torch_backend import CausalTurn
    groups=[]
    for n, rewards in enumerate(((0,1,None,None),(1,None,None,None),(1,0,1,None))):
        plan=GroupPlan(str(n),TaskSlot('task','family','feature'),'policy',(1,2,3,4),5,n)
        # This pure tensor projection fixture does not claim to establish admissions/outcomes.
        records=tuple(SimpleNamespace(costs=(i+1,),reward=r) for i,r in enumerate(rewards))
        advantages=group_advantages(rewards)
        turns=tuple((CausalTurn((1,2),(3,),(True,),(-1.,),a),) if a is not None else () for a in advantages)
        groups.append(PreparedGroup(plan,records,turns,rewards,advantages))
    original=[(g.records,tuple(r.costs for r in g.records)) for g in groups]
    rows=group_rows(groups)
    assert [r.instance_id for r in rows] == ['0','0','2','2','2']
    assert [r.reward for r in rows] == [0.,1.,1.,0.,1.]
    assert original == [(g.records,tuple(r.costs for r in g.records)) for g in groups]
    assert sum(len(g.records) for g in groups) == 12
