"""Pinned-source/config and isolated control-plane checks. No native/GPU/model call."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.training.native import NativeSettings,native_overrides,NativeSession,require_private_tree
from feature_rl.training.state import PolicyBarrier


def settings(tmp_path,**changes):
    values={key:str(tmp_path/key) for key in ('skyrl_checkout','model_directory','reference_directory','tokenizer_directory','work_directory')}
    return NativeSettings(**{**values,'tokenizer_sha256':'c'*64,'probe_tokens':(1,2),**changes})


def config():
    samples=json.loads(Path('docs/evidence/M0/examples.json').read_text())
    return c.TrainingConfig.model_validate_json(json.dumps(samples['TrainingCheckpoint']['configuration']))


def test_closed_overrides_bind_real_pinned_fields_and_external_data_hooks(tmp_path):
    cfg=config().model_copy(update={'group_size':4,'initial_policy':config().initial_policy.model_copy(update={'temperature':1.,'top_p':1.})})
    overrides=native_overrides(settings(tmp_path),cfg)
    source=Path('.feature-rl/research/M7/skyrl/skyrl/train/config/config.py')
    if not source.exists():pytest.skip('Retained exact pinned source unavailable')
    classes={node.name:{field.target.id:ast.unparse(field.annotation) for field in node.body
        if isinstance(field,ast.AnnAssign) and isinstance(field.target,ast.Name)}
        for node in ast.parse(source.read_text()).body if isinstance(node,ast.ClassDef)}
    for key in overrides:
        current='SkyRLTrainConfig'
        for part in key.split('.'):
            if current.startswith(('Dict[','dict')):break
            assert part in classes[current],key
            current=classes[current][part]
    assert overrides['trainer.update_epochs_per_batch']==1
    assert overrides['trainer.algorithm.kl_estimator_type']=='k3'
    assert overrides['trainer.logger']=='console'
    assert overrides['generator.n_samples_per_prompt']==4
    assert overrides['trainer.algorithm.policy_loss_type']=='feature_rl_sampled_clipped'
    trainer=Path('.feature-rl/research/M7/skyrl/skyrl/train/trainer.py').read_text()
    for method in ('_build_train_dataloader_and_compute_training_steps','save_checkpoints','load_checkpoints','save_models'):
        assert 'def '+method+'(' in trainer
    entry=Path('.feature-rl/research/M7/skyrl/skyrl/train/entrypoints/main_base.py').read_text()
    assert 'def _setup_trainer(' in entry and 'def get_trainer(' in entry


def test_native_settings_reject_overlapping_roots_and_invalid_batch(tmp_path):
    with pytest.raises(ValueError,match='separate'):settings(tmp_path,work_directory=str(tmp_path/'model_directory'/'work'))
    with pytest.raises(ValueError,match='divide'):settings(tmp_path,groups_per_update=3,mini_batch_groups=2)


def test_loader_rejects_writable_files_and_ancestor_redirects(tmp_path):
    root=tmp_path/'native';root.mkdir(mode=0o700);file=root/'state.pt';file.write_bytes(b'inert diagnostic')
    require_private_tree(root)
    file.chmod(0o666)
    with pytest.raises(ValueError,match='writable'):require_private_tree(root)
    file.chmod(0o600);alias=tmp_path/'alias';alias.symlink_to(root,target_is_directory=True)
    with pytest.raises(ValueError,match='redirected'):require_private_tree(alias)


def test_native_sync_probes_every_actual_endpoint_and_refuses_partial_ack(tmp_path,monkeypatch):
    import feature_rl.training.native as module
    session=object.__new__(NativeSession);session.settings=settings(tmp_path,lora=module.LoRASettings(enabled=False))
    session.barrier=PolicyBarrier(('http://one','http://two'))
    policy=config().initial_policy
    policy=policy.model_copy(update={'identity':policy.identity.model_copy(update={'weights':config().reference_checkpoint})})
    session.backend=SimpleNamespace(model_name=policy.identity.model,inference_model=policy.identity.model,
        tokenizer_digest='c'*64,template_digest='d'*64,vocab_size=256)
    calls=[]
    async def ensure():return None
    async def sync():calls.append('actual API boundary double: awaited broadcast')
    session.bridge=SimpleNamespace(_ensure_colocated_inference_asleep=ensure)
    session.trainer=SimpleNamespace(dispatch=SimpleNamespace(save_weights_for_sampler=sync))
    finish=['stop','stop']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,cap):return json.dumps({'choices':[{'token_ids':[3,4],'finish_reason':finish.pop(0)}]}).encode()
    def open_(request,timeout):
        calls.append((request.full_url,json.loads(request.data)));return Response()
    monkeypatch.setattr(module,'urlopen',open_)
    receipt=session.synchronize(policy)
    assert len(receipt['outputs'])==2 and len(calls)==3
    assert calls[1][1]['sampling_params']['temperature']==0.
    session.barrier.require(session.barrier.stamp)
    finish[:]=['stop','abort']
    with pytest.raises(ValueError,match='interrupted'):session.synchronize(policy)
    with pytest.raises(ValueError,match='incomplete'):session.barrier.require(session.barrier.stamp)


def test_synchronize_rechecks_colocated_sleep_before_broadcast(tmp_path,monkeypatch):
    import feature_rl.training.native as module
    session=object.__new__(NativeSession)
    session.settings=settings(tmp_path,lora=module.LoRASettings(enabled=False))
    session.barrier=PolicyBarrier(('http://one',))
    policy=config().initial_policy
    policy=policy.model_copy(update={'identity':policy.identity.model_copy(update={'weights':config().reference_checkpoint})})
    session.backend=SimpleNamespace(model_name=policy.identity.model,inference_model=policy.identity.model,
        tokenizer_digest='c'*64,template_digest='d'*64,vocab_size=256)
    order=[]
    async def ensure():order.append('ensure_asleep')
    async def sync():order.append('save_weights_for_sampler')
    session.bridge=SimpleNamespace(_ensure_colocated_inference_asleep=ensure)
    session.trainer=SimpleNamespace(dispatch=SimpleNamespace(save_weights_for_sampler=sync))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,cap):return json.dumps({'choices':[{'token_ids':[3],'finish_reason':'stop'}]}).encode()
    monkeypatch.setattr(module,'urlopen',lambda request,timeout: Response())
    session.synchronize(policy)
    assert order==['ensure_asleep','save_weights_for_sampler']


def test_colocated_sleep_guard_requires_native_bridge():
    session=object.__new__(NativeSession)
    with pytest.raises(RuntimeError,match='native bridge is required before colocated inference residency checks'):
        session._ensure_colocated_inference_asleep()


def _residency_session(tmp_path):
    """Real NativeSession methods with the same isolated doubles as the probe tests."""
    import feature_rl.training.native as module
    session=object.__new__(NativeSession)
    session.settings=settings(tmp_path,lora=module.LoRASettings(enabled=False))
    session.policy=config().initial_policy
    session.backend=SimpleNamespace(verify_policy=lambda policy:None)
    session.barrier=SimpleNamespace(stamp=('stamp',),probe=(1,),begin=lambda *args:None)
    session.last_probe={'outputs':{}}
    snapshot=[{'rank':0,'trainable_parameters':{'w':{'dtype':'float32','shape':[1],'sha256':'ab'}},
        'optimizer_sha256':'cd'}]
    session.worker_snapshot=lambda:snapshot
    root=tmp_path/'native-output'
    session.trainer=SimpleNamespace(global_step=0,cfg=SimpleNamespace(trainer=SimpleNamespace(
        export_path=str(root/'export'),ckpt_path=str(root/'ckpt'))))
    return session


def test_update_export_sleeps_before_save_models(tmp_path):
    session=_residency_session(tmp_path)
    order=[]
    async def bridge_update(rows,*,algorithm):
        return {'grad_norm':1.}
    async def ensure():
        order.append('ensure_asleep')
    def save_models():
        order.append('save_models')
        raise RuntimeError('export boundary')
    session.bridge=SimpleNamespace(update=bridge_update,_ensure_colocated_inference_asleep=ensure)
    session.trainer.save_models=save_models
    with pytest.raises(RuntimeError,match='export boundary'):
        session.update([],algorithm='grpo')
    assert order==['ensure_asleep','save_models']


def test_update_export_does_not_save_models_when_sleep_guard_fails(tmp_path):
    session=_residency_session(tmp_path)
    order=[]
    async def bridge_update(rows,*,algorithm):
        return {'grad_norm':1.}
    async def ensure():
        order.append('ensure_asleep')
        raise RuntimeError('colocated inference engine did not enter sleep state before policy backload')
    def save_models():
        order.append('save_models')
    session.bridge=SimpleNamespace(update=bridge_update,_ensure_colocated_inference_asleep=ensure)
    session.trainer.save_models=save_models
    with pytest.raises(RuntimeError,match='did not enter sleep state before policy backload'):
        session.update([],algorithm='grpo')
    assert order==['ensure_asleep']
    assert 'save_models' not in order


def test_save_reload_sleeps_before_checkpoints(tmp_path):
    session=_residency_session(tmp_path)
    order=[]
    async def ensure():
        order.append('ensure_asleep')
    def save_checkpoints():
        order.append('save_checkpoints')
        raise RuntimeError('checkpoint boundary')
    session.bridge=SimpleNamespace(_ensure_colocated_inference_asleep=ensure)
    session.trainer.save_checkpoints=save_checkpoints
    with pytest.raises(RuntimeError,match='checkpoint boundary'):
        session.save_reload()
    assert order==['ensure_asleep','save_checkpoints']


def test_save_reload_does_not_checkpoint_when_sleep_guard_fails(tmp_path):
    session=_residency_session(tmp_path)
    order=[]
    async def ensure():
        order.append('ensure_asleep')
        raise RuntimeError('colocated inference engine did not enter sleep state before policy backload')
    def save_checkpoints():
        order.append('save_checkpoints')
    session.bridge=SimpleNamespace(_ensure_colocated_inference_asleep=ensure)
    session.trainer.save_checkpoints=save_checkpoints
    with pytest.raises(RuntimeError,match='did not enter sleep state before policy backload'):
        session.save_reload()
    assert order==['ensure_asleep']
    assert 'save_checkpoints' not in order


def test_activation_receipt_binds_checkpoint_and_requires_live_barrier(tmp_path,monkeypatch):
    from test_agent_runner import fixture
    from feature_rl.training.files import publish_directory
    from feature_rl.training.native import read_activation
    from feature_rl.training.state import PolicyStamp
    from dataclasses import asdict
    f=fixture(tmp_path,monkeypatch)
    session=object.__new__(NativeSession);session.settings=settings(tmp_path)
    root=Path(session.settings.model_directory);root.mkdir(mode=0o700);(root/'model.safetensors').write_bytes(b'inert diagnostic only')
    weights=publish_directory(store=f.store,registry=f.registry,path=root)
    policy=f.policy.model_copy(update={'identity':f.policy.identity.model_copy(update={'weights':weights,'provider':'skyrl'})})
    session.configuration=config().model_copy(update={'initial_policy':policy,'reference_checkpoint':weights,'tasks':(f.task,)})
    session.store=f.store;session.registry=f.registry;session.revision='e'*40;session.policy=policy
    session.barrier=PolicyBarrier(('http://diagnostic',))
    stamp=PolicyStamp(policy.policy_version,weights.sha256,'c'*64,'d'*64)
    def synchronize(value):
        session.barrier.begin(stamp,(3,));session.barrier.acknowledge('http://diagnostic',stamp,(3,))
        session.policy=value;return {'stamp':asdict(stamp),'outputs':{'http://diagnostic':(3,)},'scope':'unit_diagnostic'}
    monkeypatch.setattr(session,'synchronize',synchronize)
    session.backend=SimpleNamespace(model_name=policy.identity.model,tokenizer_digest='c'*64,template_digest='d'*64,
        verify_policy=lambda value:session.barrier.require(PolicyStamp(value.policy_version,value.identity.weights.sha256,'c'*64,'d'*64)))
    ref=session.activate_checkpoint(weights,policy)
    receipt=read_activation(f.store,ref)
    assert receipt.checkpoint==weights and receipt.policy==policy
    assert session.validate_activation(ref,checkpoint=weights,policy=policy)==receipt
    session.barrier.begin(stamp,(3,))
    with pytest.raises(ValueError,match='incomplete'):session.validate_activation(ref,checkpoint=weights,policy=policy)
