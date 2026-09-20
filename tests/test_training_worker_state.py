import copy
import pytest
from feature_rl.training.worker_state import snapshot_state,compare_states


def test_content_witness_ignores_export_metadata_and_binds_optimizer_and_reload():
    import torch
    torch.manual_seed(1);model=torch.nn.Linear(3,2,dtype=torch.bfloat16)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.01)
    before=snapshot_state(model,optimizer,rank=0)
    model(torch.ones(2,3,dtype=torch.bfloat16)).float().square().mean().backward();optimizer.step()
    after=snapshot_state(model,optimizer,rank=0)
    changed=compare_states([before],[after])
    assert changed['changed_trainable_shards']>0 and changed['changed_optimizer_shards']==1
    saved_model=copy.deepcopy(model.state_dict());saved_optimizer=copy.deepcopy(optimizer.state_dict())
    optimizer.zero_grad();model(torch.zeros(2,3,dtype=torch.bfloat16)).float().sum().backward();optimizer.step()
    model.load_state_dict(saved_model);optimizer.load_state_dict(saved_optimizer)
    assert snapshot_state(model,optimizer,rank=0)==after
    assert compare_states([after],[after])=={'changed_trainable_shards':0,'changed_optimizer_shards':0}
    altered=copy.deepcopy(after);altered['rank']=1
    with pytest.raises(ValueError,match='roster'):compare_states([after],[altered])
