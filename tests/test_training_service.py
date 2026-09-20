"""Actual service/CAS/Registry with explicit native/admission diagnostic boundaries.

CPU Torch performs real parameter updates and exact optimizer/RNG save/reload.
No native stack, model generation, HumanReview or accepted task is claimed.
"""
from pathlib import Path
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.training.service import TrainingService
from feature_rl.training.native import NativeSettings
from feature_rl.training.torch_backend import CausalTurn,TorchUpdater
from feature_rl.training.files import publish_directory


def service_fixture(tmp_path,monkeypatch,*,collected_groups=True):
    from test_agent_runner import fixture
    from m4_fixtures import replace_artifact
    from feature_rl.training.skyrl_bridge import PINNED_SKYRL,PINNED_HARBOR
    import feature_rl.training.service as service_module
    import torch
    f=fixture(tmp_path,monkeypatch)
    task=replace_artifact(f.store,f.task,partition=c.Partition.TRAIN)
    directory=tmp_path/'model';directory.mkdir();(directory/'model.pt').write_bytes(b'inert initial manifest')
    weights=publish_directory(store=f.store,registry=f.registry,path=directory)
    policy=f.policy.model_copy(update={'identity':f.policy.identity.model_copy(update={'weights':weights})})
    config=c.TrainingConfig(initial_policy=policy,reference_checkpoint=weights,tasks=(task,),limits=f.limits,
        seeds=c.SeedPolicy(algorithm='diagnostic',seeds=(19,),same_cases_within_group=True),algorithm='grpo',
        group_size=4,max_updates=1,learning_rate=.01,framework='skyrl',framework_version=PINNED_SKYRL,
        backend_version=PINNED_HARBOR,budget_usd=None)
    settings=NativeSettings(skyrl_checkout=str(tmp_path/'checkout'),model_directory=str(directory),
        reference_directory=str(tmp_path/'reference'),tokenizer_directory=str(tmp_path/'tokenizer'),
        work_directory=str(tmp_path/'work'),tokenizer_sha256='c'*64,probe_tokens=(1,2),max_seq_len=8192)
    calls=[]
    class Model(torch.nn.Module):
        def __init__(self):super().__init__();self.embedding=torch.nn.Embedding(8,4);self.head=torch.nn.Linear(4,8)
        def forward(self,input_ids,attention_mask=None):return SimpleNamespace(logits=self.head(self.embedding(input_ids)))
    class CPU:
        def __init__(self,**kwargs):
            import json
            journals=list((Path(kwargs['settings'].work_directory).parents[1]/'controller').glob('*.json'))
            assert any(json.loads(path.read_text())['phase']=='initializing' for path in journals)
            self.output=Path(kwargs['settings'].work_directory);self.output.mkdir(parents=True,exist_ok=True)
            calls.append('initialize');torch.manual_seed(4);self.updater=TorchUpdater(Model(),learning_rate=.01,kl_coefficient=0,max_seq_len=8192)
            self.policy=config.initial_policy;self.backend=f.backend;self.trainer=SimpleNamespace(global_step=0)
            self.last_probe={'scope':'unit_diagnostic','no_native_inference':True}
        def update(self,rows,algorithm):
            from feature_rl.training.worker_state import snapshot_state,compare_states
            before=snapshot_state(self.updater.model,self.updater.optimizer,rank=0)
            calls.append('update');receipt=self.updater.update([row.turn for row in rows],algorithm=algorithm)
            after=snapshot_state(self.updater.model,self.updater.optimizer,rank=0)
            self.trainer.global_step+=1
            path=self.output/('export-'+str(self.trainer.global_step));path.mkdir()
            torch.save(self.updater.model.state_dict(),path/'model.pt')
            ref=publish_directory(store=f.store,registry=f.registry,path=path)
            self.policy=self.policy.model_copy(update={'identity':self.policy.identity.model_copy(update={'weights':ref}),
                'policy_version':'diagnostic-step-'+str(self.trainer.global_step)})
            return {'grad_norm':receipt.gradient_norm,'weights':ref.model_dump(mode='json'),'cpu_tensor_before':receipt.before,'cpu_tensor_after':receipt.after,
                **compare_states([before],[after])}
        def save_reload(self):
            calls.append('save-reload');path=self.output/('checkpoint-'+str(self.trainer.global_step))
            digest=self.updater.save_checkpoint(path,binding={'diagnostic':True},progress={'position':1},policy_version=self.policy.policy_version)
            self.updater.load_checkpoint(path,expected_digest=digest,binding={'diagnostic':True})
            ref=publish_directory(store=f.store,registry=f.registry,path=path)
            return {'checkpoint':ref.model_dump(mode='json'),'path':str(path),'global_step':self.trainer.global_step,
                'diagnostic_updater_digest':digest,'scope':'unit_diagnostic'}
        def resume(self,checkpoint,path,policy):
            calls.append('resume');self.policy=policy
            import hashlib
            from feature_rl.training.files import verify_directory
            verify_directory(store=f.store,ref=checkpoint,path=Path(path))
            metadata=self.updater.load_checkpoint(Path(path),expected_digest=hashlib.sha256((Path(path)/'manifest.json').read_bytes()).hexdigest(),binding={'diagnostic':True})
            self.trainer.global_step=metadata['optimizer_steps']
            return self.last_probe
        def close(self):calls.append('close')
    monkeypatch.setattr(service_module,'NativeSession',CPU)
    import feature_rl.training.factory as factory_module
    monkeypatch.setattr(factory_module,'NativeSession',CPU)
    service=TrainingService(store=f.store,registry=f.registry,lifecycle=f.runner.lifecycle,builder=f.runner.builder,
        runtime=f.runner.runtime,grader=f.runner.grader,settings=settings,revision='f'*40,evidence_scope='unit_diagnostic')
    if collected_groups:
        # Only this controller/update fixture substitutes collected groups.
        from dataclasses import asdict
        from feature_rl.training.data import PreparedGroup
        groups={}
        def collect(sampler):
            if service.state['unclassified_group'] is not None:
                return groups[service.state['unclassified_group']['sha256']]
            plan=sampler.next_group(service.native.policy.policy_version)
            turns=[]
            for target,advantage in ((3,.5),(4,-.5),(3,.5),(4,-.5)):
                raw=CausalTurn((1,2),(target,),(True,))
                behavior=tuple(service.native.updater.logprobs(raw).detach().tolist())
                turns.append((CausalTurn(raw.context,raw.targets,raw.mask,behavior,advantage),))
            group=PreparedGroup(plan,(),tuple(turns),(1,0,1,0),(.5,-.5,.5,-.5))
            receipt=service._put({'diagnostic':True,'plan':asdict(plan)},'m7-assigned-group',(task,))
            groups[receipt.sha256]=group
            service.state['sampler']=sampler.state_dict()
            service.state['groups']+=1;service.state['data_position']=sampler.position
            service.state['group_receipts'].append(receipt.model_dump(mode='json'))
            service.state['unclassified_group']=receipt.model_dump(mode='json')
            service.state['consumed']=[task.model_dump(mode='json')]
            service._write();return group
        monkeypatch.setattr(service,'_collect',collect)
        monkeypatch.setattr(service,'_prepared_from_ref',lambda ref:groups[ref.sha256])
    return SimpleNamespace(service=service,config=config,calls=calls,fixture=f)


def test_real_cpu_grpo_service_update_checkpoint_and_selected_job_replay(tmp_path,monkeypatch):
    f=service_fixture(tmp_path,monkeypatch)
    result=f.service.train(f.config,invocation='diagnostic-grpo')
    assert result.disposition==c.Disposition.SUCCESS
    checkpoint=f.fixture.store.get_artifact(result.artifacts[0])
    assert isinstance(checkpoint,c.TrainingCheckpoint) and checkpoint.optimizer_steps==1
    assert checkpoint.data_position==1 and checkpoint.weights!=f.config.initial_policy.identity.weights
    assert checkpoint.update_evidence[0].scope=='unit_diagnostic'
    from feature_rl.training.checkpoints import validate_selected_checkpoint
    assert validate_selected_checkpoint(f.fixture.store,f.fixture.registry,result.artifacts[0],configuration=f.config)==checkpoint
    altered=f.fixture.store.put_artifact(checkpoint.model_copy(update={'optimizer_steps':2}))
    f.fixture.registry.register(altered)
    with pytest.raises(ValueError,match='sole selected'):validate_selected_checkpoint(f.fixture.store,f.fixture.registry,altered)
    assert f.calls==['initialize','update','save-reload','close']
    assert all(cost.usd is None for cost in result.costs)
    assert f.service.train(f.config,invocation='diagnostic-grpo')==result
    assert f.calls==['initialize','update','save-reload','close']


def test_admission_and_currency_gates_precede_native(tmp_path,monkeypatch):
    f=service_fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='actual native cost meter'):f.service.train(f.config.model_copy(update={'budget_usd':1.}),invocation='budget')
    def denied(ref):raise ValueError('actual current admission denied')
    monkeypatch.setattr(f.service.lifecycle,'resolve_released',denied)
    with pytest.raises(ValueError,match='admission denied'):f.service.train(f.config,invocation='denied')
    assert not f.calls


def test_grpo_assigns_four_real_runner_jobs_same_cases_and_stops_without_signal(tmp_path,monkeypatch):
    from feature_rl.agents import AgentRunner
    f=service_fixture(tmp_path,monkeypatch,collected_groups=False)
    f.service.settings=f.service.settings.model_copy(update={'max_groups':1})
    # Only public-package/admission/native boundaries are substituted. Actual
    # runner jobs, saved submission, selected diagnostic M4 receipts and gate run.
    monkeypatch.setattr(AgentRunner,'_messages',lambda self,task:[{'role':'user','content':'DIAGNOSTIC public instruction only'}])
    f.fixture.backend.outputs=['{"action":"submit"}']*4
    result=f.service.train(f.config,invocation='diagnostic-grpo')
    assert result.disposition==c.Disposition.BLOCKED
    assert f.calls==['initialize','close']
    assert len(f.fixture.backend.calls)==len(f.fixture.grades)==4
    assert len({call[1].seed for call in f.fixture.backend.calls})==4
    assert len({call[2] for call in f.fixture.grades})==1
    assert len(f.service.state['group_receipts'])==1 and f.service.state['groups']==1
    assert not any(ref.kind=='TrainingCheckpoint' for ref in result.artifacts)


def test_interrupted_collection_recovers_same_grpo_batch_without_resampling(tmp_path,monkeypatch):
    f=service_fixture(tmp_path,monkeypatch)
    update=f.service._update
    interrupted=[]
    def once(rows):
        if not interrupted:interrupted.append(True);raise OSError('diagnostic interruption before update dispatch')
        return update(rows)
    monkeypatch.setattr(f.service,'_update',once)
    with pytest.raises(OSError,match='before update'):f.service.train(f.config,invocation='interrupted')
    assert f.service.state['data_position']==1 and len(f.service.state['batch_receipts'])==1
    assert f.calls==['initialize','close']
    result=f.service.train(f.config,invocation='interrupted')
    assert result.disposition==c.Disposition.SUCCESS
    assert f.service.state['data_position']==1 and f.service.state['updates']==1
    assert f.calls==['initialize','close','initialize','update','save-reload','close']


def test_checkpointed_service_resume_restores_real_cpu_optimizer_and_next_position(tmp_path,monkeypatch):
    f=service_fixture(tmp_path,monkeypatch);f.config=f.config.model_copy(update={'max_updates':2})
    update=f.service._update;attempts=[]
    def interrupted(rows):
        attempts.append(True)
        if len(attempts)==2:raise OSError('diagnostic between checkpoint and next update')
        return update(rows)
    monkeypatch.setattr(f.service,'_update',interrupted)
    with pytest.raises(OSError,match='between checkpoint'):f.service.train(f.config,invocation='resume-cpu')
    assert f.service.state['updates']==1 and f.service.state['data_position']==2
    result=f.service.train(f.config,invocation='resume-cpu')
    checkpoint=f.fixture.store.get_artifact(result.artifacts[0])
    assert result.disposition==c.Disposition.SUCCESS and checkpoint.optimizer_steps==2 and checkpoint.data_position==2
    assert f.calls==['initialize','update','save-reload','close','initialize','resume','update','save-reload','close']


def test_explicit_recovery_skips_unknown_update_and_restores_cpu_optimizer(tmp_path,monkeypatch):
    from feature_rl.training.checkpoints import validate_selected_checkpoint
    from feature_rl.artifacts import canonical_json
    import json
    f=service_fixture(tmp_path,monkeypatch);f.config=f.config.model_copy(update={'max_updates':3})
    original=f.service._update
    def interrupt(rows):
        if f.service.state['updates']==1:
            real=f.service.native.update
            def lost(*args,**kwargs):
                real(*args,**kwargs)
                raise OSError('lost later update result')
            f.service.native.update=lost
        return original(rows)
    monkeypatch.setattr(f.service,'_update',interrupt)
    with pytest.raises(OSError,match='lost later'):f.service.train(f.config,invocation='old')
    old_claim=f.service.claim;old_accounting=f.fixture.registry.accounting(old_claim.job_id)
    checkpoint=c.ArtifactRef.model_validate_json(canonical_json(f.service.state['checkpoint']))
    old_export=Path(f.service.settings.work_directory)/'jobs'/old_claim.job_id/'export-2'/'model.pt'
    old_bytes=old_export.read_bytes()
    from feature_rl.training.checkpoints import validate_recovery_checkpoint
    original_cp=f.fixture.store.get_artifact(checkpoint)
    forged=f.fixture.store.put_artifact(original_cp.model_copy(update={'costs':(original_cp.costs[0].model_copy(update={'note':'diagnostic counterfeit cost'}),)}))
    f.fixture.registry.register(forged)
    with pytest.raises(ValueError,match='confirmed update observation'):
        validate_recovery_checkpoint(f.fixture.store,f.fixture.registry,forged,configuration=f.config)
    with pytest.raises(ValueError,match='sole selected'):validate_selected_checkpoint(f.fixture.store,f.fixture.registry,checkpoint)
    monkeypatch.setattr(f.service,'_update',original)
    result=f.service.train(f.config,invocation='explicit-recovery',resume=checkpoint)
    recovered=f.fixture.store.get_artifact(result.artifacts[0])
    assert recovered.optimizer_steps==2 and recovered.data_position==3
    assert f.service.state['unknown_updates']==1 and f.service.state['updates']==2
    assert f.service.claim.job_id!=old_claim.job_id
    assert old_export.read_bytes()==old_bytes
    assert f.fixture.registry.accounting(old_claim.job_id)==old_accounting
    assert f.calls.count('update')==3 and f.calls.count('resume')==1
    assert result.disposition==c.Disposition.SUCCESS


def test_terminal_training_cleanup_closes_unpublished_startup_after_quarantine(tmp_path,monkeypatch):
    from feature_rl.artifacts import ArtifactError
    f=service_fixture(tmp_path,monkeypatch);put=f.fixture.store.put_bytes;failed=[]
    def outage(payload,kind,*args,**kwargs):
        if kind=='m7-native-startup' and not failed:failed.append(True);raise ArtifactError('startup receipt outage')
        return put(payload,kind,*args,**kwargs)
    monkeypatch.setattr(f.fixture.store,'put_bytes',outage)
    with pytest.raises(ArtifactError):f.service.train(f.config,invocation='terminal-cleanup')
    assert f.calls==['initialize']
    f.fixture.registry.quarantine(f.config.tasks[0],notice_id='terminal',reason='diagnostic quarantine',
        evidence=(f.service.request,))
    f.service.close();f.service.close()
    assert f.calls==['initialize','close']
    rows=f.fixture.registry.accounting(f.service.claim.job_id).observations
    assert any(o.observation.source=='m7-native-startup' and o.observation.revision==1 for o in rows)
    assert any(o.observation.source=='m7-native-shutdown' and o.observation.revision==2 for o in rows)


def test_explicit_recovery_exhausted_unknown_budget_is_blocked_without_old_checkpoint_selection(tmp_path,monkeypatch):
    f=service_fixture(tmp_path,monkeypatch);f.config=f.config.model_copy(update={'max_updates':2})
    original=f.service._update
    def interrupted(rows):
        if f.service.state['updates']==1:
            real=f.service.native.update
            def lost(*args,**kwargs):
                real(*args,**kwargs);raise OSError('lost budget-ending update')
            f.service.native.update=lost
        return original(rows)
    monkeypatch.setattr(f.service,'_update',interrupted)
    with pytest.raises(OSError):f.service.train(f.config,invocation='budget-old')
    ref=c.ArtifactRef.model_validate_json(canonical_json(f.service.state['checkpoint']))
    monkeypatch.setattr(f.service,'_update',original)
    result=f.service.train(f.config,invocation='budget-recovery',resume=ref)
    assert result.disposition==c.Disposition.BLOCKED
    assert not any(r.kind=='TrainingCheckpoint' for r in result.artifacts)
    assert f.service.state['updates']==1 and f.service.state['unknown_updates']==1
    assert f.calls.count('update')==2 and f.calls.count('initialize')==1 # No restart, replay or budget extension.
