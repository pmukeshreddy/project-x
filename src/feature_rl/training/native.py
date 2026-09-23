"""Pinned single-node FSDP/vLLM construction, synchronization and native persistence.

All CUDA/framework imports are lazy. This code is source-bound, not an assertion
that the pinned stack has executed here. Harbor Trial/artifact transfer is unused:
the external AgentRunner supplies source-only, separately graded trajectories.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import stat
import subprocess
from typing import Annotated, Literal
from urllib.request import Request,urlopen
from pydantic import Field,model_validator
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.agents.backend import SkyRLTokenBackend
from feature_rl.agents.protocol import HARNESS
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_local
from .files import inspect_directory,publish_directory,verify_directory
from .state import PolicyBarrier,PolicyStamp
from .skyrl_bridge import (PINNED_SKYRL,PINNED_HARBOR,GRPO_LOSS,
    SkyRLUpdateBridge,register_losses,verify_installed_pins)


class ActivationReceipt(c.StrictModel):
    version: Literal['m7-native-activation-v1']='m7-native-activation-v1'
    checkpoint: c.ArtifactRef
    policy: c.PolicyConfig
    configuration: c.ArtifactRef
    probe: c.ArtifactRef
    workers: tuple[str,...]
    tokenizer_sha256: c.Digest
    template_sha256: c.Digest
    implementation_revision: c.Revision
    recorded_at: c.UTCDateTime


def read_activation(store,ref):
    return read_local(store,ref,ActivationReceipt,'m7-native-activation',4*1024*1024)


class LoRASettings(c.StrictModel):
    enabled: bool = True
    rank: Literal[8,16,32,64] = 16
    alpha: Annotated[int,Field(gt=0,le=4096)] = 32
    dropout: Annotated[float,Field(ge=0,lt=1)] = 0.0
    target_modules: Annotated[str,Field(min_length=1,max_length=4096)] = 'all-linear'
    exclude_modules: Annotated[str,Field(min_length=1,max_length=4096)] | None = None
    # Use the pinned FSDP wrapper's default zero-update initialization, so the
    # initial adapter represents the same frozen base policy before training.
    init_method: Literal['kaiming'] = 'kaiming'


class NativeSettings(c.StrictModel):
    version: Literal['m7-native-settings-v1']='m7-native-settings-v1'
    skyrl_checkout: str
    model_directory: str
    reference_directory: str
    tokenizer_directory: str
    work_directory: str
    tokenizer_sha256: c.Digest
    num_gpus: Annotated[int,Field(ge=1,le=8)]=1
    max_seq_len: Annotated[int,Field(ge=128,le=131072)]=8192
    groups_per_update: Annotated[int,Field(ge=1,le=64)]=1
    mini_batch_groups: Annotated[int,Field(ge=1,le=64)]=1
    clip_epsilon: Annotated[float,Field(gt=0,lt=1)]=0.2
    kl_coefficient: c.NonnegativeFloat=0.001
    probe_tokens: Annotated[tuple[Annotated[int,Field(ge=0)],...],Field(min_length=1,max_length=256)]
    probe_output_tokens: Annotated[int,Field(ge=1,le=32)]=8
    probe_timeout_seconds: Annotated[float,Field(gt=0,le=120)]=60.
    max_groups: Annotated[int,Field(ge=1,le=100000)]=64
    max_wall_seconds: Annotated[float,Field(gt=0,le=604800)]=3600.
    lora: LoRASettings = Field(default_factory=LoRASettings)

    @model_validator(mode='after')
    def paths_and_batches(self):
        paths=[Path(getattr(self,k)) for k in ('skyrl_checkout','model_directory','reference_directory','tokenizer_directory','work_directory')]
        if any(not p.is_absolute() for p in paths): raise ValueError('Explicit absolute native paths required')
        if self.groups_per_update%self.mini_batch_groups: raise ValueError('Configured full minibatches must divide collection groups')
        if any(paths[4]==p or paths[4].is_relative_to(p) or p.is_relative_to(paths[4]) for p in paths[1:4]):
            raise ValueError('Native work directory must be separate from immutable model/reference/tokenizer')
        return self


def native_overrides(settings:NativeSettings,config:c.TrainingConfig):
    """Closed overrides of actual pinned dataclass fields; no arbitrary plugins/commands."""
    if type(config.group_size) is not int or config.group_size<2:
        raise ValueError('At least two assigned GRPO episodes required')
    if config.initial_policy.temperature!=1. or config.initial_policy.top_p!=1.:
        raise ValueError('Unit-temperature/full-support native training required for exact behavior probabilities')
    if settings.lora.enabled and settings.lora.dropout!=0:
        raise ValueError('GRPO requires zero LoRA dropout for inference/training probability alignment')
    work=Path(settings.work_directory)
    return {
        'trainer.strategy':'fsdp','trainer.policy.model.path':settings.model_directory,
        'trainer.ref.model.path':settings.reference_directory,'trainer.critic.model.path':None,
        'trainer.policy.optimizer_config.lr':config.learning_rate,
        'trainer.policy.optimizer_config.scheduler':'constant_with_warmup',
        'trainer.policy.optimizer_config.num_warmup_steps':0,
        'trainer.policy.model.lora.rank':settings.lora.rank if settings.lora.enabled else 0,
        'trainer.policy.model.lora.alpha':settings.lora.alpha,
        'trainer.policy.model.lora.dropout':settings.lora.dropout,
        'trainer.policy.model.lora.target_modules':settings.lora.target_modules,
        'trainer.policy.model.lora.exclude_modules':settings.lora.exclude_modules,
        'trainer.policy.model.lora.init_method':settings.lora.init_method,
        'trainer.policy.model.lora.lora_sync_path':str(work/'lora-sync'),
        'trainer.policy.model.lora.max_loras':1,
        'trainer.ref.model.lora.rank':0,
        'trainer.placement.colocate_all':True,
        'trainer.placement.policy_num_nodes':1,'trainer.placement.ref_num_nodes':1,
        'trainer.placement.policy_num_gpus_per_node':settings.num_gpus,
        'trainer.placement.ref_num_gpus_per_node':settings.num_gpus,
        'trainer.train_batch_size':settings.groups_per_update,
        'trainer.policy_mini_batch_size':settings.mini_batch_groups,
        'trainer.micro_train_batch_size_per_gpu':1,'trainer.micro_forward_batch_size_per_gpu':1,
        'trainer.update_epochs_per_batch':1,'trainer.epochs':1,
        'trainer.max_training_steps':config.max_updates,'trainer.seed':config.seeds.seeds[0],
        'trainer.max_prompt_length':settings.max_seq_len-1,
        'trainer.algorithm.max_seq_len':settings.max_seq_len,
        'trainer.algorithm.policy_loss_type':GRPO_LOSS,
        'trainer.algorithm.advantage_estimator':'grpo',
        'trainer.algorithm.loss_reduction':'token_mean',
        'trainer.algorithm.advantage_batch_normalize':False,
        'trainer.algorithm.grpo_norm_by_std':False,
        'trainer.algorithm.zero_variance_filter':False,
        'trainer.algorithm.dynamic_sampling.type':None,
        'trainer.algorithm.temperature':1.,
        'trainer.algorithm.eps_clip_low':settings.clip_epsilon,
        'trainer.algorithm.eps_clip_high':settings.clip_epsilon,
        'trainer.algorithm.use_kl_in_reward':False,
        'trainer.algorithm.kl_estimator_type':'k3',
        'trainer.algorithm.use_kl_loss':settings.kl_coefficient>0,
        'trainer.algorithm.kl_loss_coef':settings.kl_coefficient,
        'trainer.update_ref_every_epoch':False,'trainer.fully_async.simulate_training':False,
        'trainer.resume_mode':'none','trainer.max_ckpts_to_keep':-1,
        'trainer.ckpt_path':str(work/'checkpoints'),'trainer.export_path':str(work/'exports'),
        'trainer.log_path':str(work/'logs'),'trainer.logger':'console',
        'trainer.eval_interval':-1,'trainer.eval_before_train':False,
        'trainer.dump_data_batch':False,'trainer.dump_eval_results':False,
        'trainer.print_example_interval':-1,'trainer.num_logger_train_samples':-1,
        'trainer.enable_ray_gpu_monitor':False,
        'generator.n_samples_per_prompt':config.group_size,'generator.step_wise_trajectories':True,
        'generator.merge_stepwise_output':False,'generator.apply_overlong_filtering':False,
        'generator.sampling_params.temperature':1.,'generator.sampling_params.top_p':1.,
        'generator.inference_engine.num_engines':1,
        'generator.inference_engine.tensor_parallel_size':settings.num_gpus,
        'generator.inference_engine.served_model_name':config.initial_policy.identity.model,
        'generator.inference_engine.enable_ray_prometheus_stats':False,
        'generator.inference_engine.enforce_eager':not settings.lora.enabled,
        'generator.inference_engine.engine_init_kwargs.max_model_len':settings.max_seq_len,
    }


def _trusted_model(path):
    """Pinned workers use trust_remote_code=True: reject dynamic model code bundles."""
    for file in Path(path).rglob('*'):
        if file.suffix=='.py': raise ValueError('Dynamic Python model code is outside this native profile')
        if file.suffix=='.json':
            value=decode_json(file.read_bytes(),64*1024*1024)
            def check(x):
                if isinstance(x,dict):
                    if 'auto_map' in x and x['auto_map']: raise ValueError('Dynamic auto_map model/tokenizer unsupported')
                    for v in x.values(): check(v)
                elif isinstance(x,list):
                    for v in x: check(v)
            check(value)


def require_private_tree(path:Path):
    """Reject redirects or untrusted writers before native model/pickle loading."""
    path=Path(path)
    if not path.is_absolute():raise ValueError('Absolute trusted native path required')
    for ancestor in (path,*path.parents):
        value=ancestor.lstat()
        if (stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode)
            or value.st_mode&0o022 or value.st_uid not in (0,os.getuid())):
            raise ValueError('Native root has redirected or writable/untrusted ancestry')
    if path.stat().st_uid!=os.getuid():raise ValueError('Native directory must be owned by the controller user')
    for file in path.rglob('*'):
        value=file.lstat()
        if (stat.S_ISLNK(value.st_mode) or not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode))
            or value.st_mode&0o022 or value.st_uid!=os.getuid()):
            raise ValueError('Native file is redirected, writable by others or not controller-owned')


class NativeSession:
    def __init__(self,*,store,registry,settings:NativeSettings,configuration:c.TrainingConfig,revision:str):
        self.settings=NativeSettings.model_validate_json(settings.model_dump_json())
        self.configuration=c.TrainingConfig.model_validate_json(configuration.model_dump_json())
        self.store,self.registry=store,registry
        if len(revision) not in (40,64) or any(x not in '0123456789abcdef' for x in revision):raise ValueError('Exact native adapter revision required')
        self.revision=revision
        policy=configuration.initial_policy
        if (configuration.framework_version!=PINNED_SKYRL or configuration.backend_version!=PINNED_HARBOR
            or configuration.framework!='skyrl' or policy.identity.provider!='skyrl' or policy.identity.weights is None
            or policy.identity.tokenizer_digest!=settings.tokenizer_sha256 or not policy.require_token_probabilities
            or policy.system_prompt.visibility!=c.Visibility.PUBLIC or policy.harness_version!=HARNESS):
            raise ValueError('Exact pinned framework, policy weights/tokenizer and training probabilities required')
        for ref,path in ((policy.identity.weights,settings.model_directory),(configuration.reference_checkpoint,settings.reference_directory)):
            require_private_tree(Path(path))
            registry.assert_usable(ref);verify_directory(store=store,ref=ref,path=Path(path));_trusted_model(path)
        require_private_tree(Path(settings.tokenizer_directory))
        tokenizer_files=inspect_directory(Path(settings.tokenizer_directory)).files
        if (len(tokenizer_files)>1024 or sum(f.size for f in tokenizer_files)>128*1024*1024
            or any(f.size>64*1024*1024 for f in tokenizer_files)
            or hashlib.sha256(canonical_json({f.path:f.sha256 for f in tokenizer_files})).hexdigest()!=settings.tokenizer_sha256):
            raise ValueError('Exact bounded tokenizer bundle required before native construction')
        _trusted_model(settings.tokenizer_directory)
        Path(settings.work_directory).mkdir(mode=0o700,parents=True,exist_ok=True)
        require_private_tree(Path(settings.work_directory))
        if settings.lora.enabled:
            (Path(settings.work_directory)/'lora-sync').mkdir(mode=0o700,exist_ok=True)
        checkout=Path(settings.skyrl_checkout)
        head=subprocess.run(['git','-C',str(checkout),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        if head!=PINNED_SKYRL: raise ValueError('Exact SkyRL source checkout required')
        subprocess.run(['git','-C',str(checkout),'diff','--quiet','HEAD','--'],check=True)
        if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64'):
            raise RuntimeError('Native execution requires the declared Linux x86_64 NVIDIA CUDA controller; arm64 is the M3 worker only')
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        import torch
        if not torch.cuda.is_available() or torch.cuda.device_count()<settings.num_gpus:
            raise RuntimeError('Native execution requires the configured available NVIDIA CUDA devices')
        import skyrl
        if not Path(skyrl.__file__).resolve().is_relative_to(checkout): raise ValueError('Imported SkyRL is outside pinned checkout')
        verify_installed_pins()
        from skyrl.train.config import SkyRLTrainConfig
        from skyrl.train.utils import validate_cfg
        from skyrl.train.utils.utils import initialize_ray
        import ray
        if ray.is_initialized():raise ValueError('NativeSession requires its own controller process/Ray connection; reuse its existing session for other arms')
        cfg=SkyRLTrainConfig.from_cli_overrides([k+'='+json.dumps(v) for k,v in native_overrides(settings,configuration).items()])
        # Registration must precede validation/worker construction for custom losses.
        initialize_ray(cfg);register_losses();validate_cfg(cfg)
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(settings.tokenizer_directory,local_files_only=True,trust_remote_code=False)
        from skyrl.train.entrypoints.main_base import BasePPOExp
        from skyrl.train.trainer import RayPPOTrainer
        from skyrl.backends.skyrl_train.workers.fsdp.fsdp_worker import FSDPPolicyWorkerBase
        import ray

        class VerifiedPolicyWorker(FSDPPolicyWorkerBase):
            def init_model(inner, model_path, num_training_steps=None):
                super().init_model(model_path,num_training_steps=num_training_steps)
                from feature_rl.training.worker_state import trainable_binding
                trainable_binding(inner.model,inner.optimizer,lora=inner._is_lora,trim_optimizer=True)

            def feature_rl_trainable_binding(inner):
                from feature_rl.training.worker_state import trainable_binding
                return {'rank':torch.distributed.get_rank(),
                    'parameters':trainable_binding(inner.model,inner.optimizer,lora=inner._is_lora)}

            def feature_rl_snapshot(inner):
                from feature_rl.training.worker_state import snapshot_state,trainable_binding
                trainable_binding(inner.model,inner.optimizer,lora=inner._is_lora)
                return snapshot_state(inner.model,inner.optimizer,rank=torch.distributed.get_rank())

        verified_policy=ray.remote(num_gpus=1)(VerifiedPolicyWorker)

        class ExternalTrainer(RayPPOTrainer):
            def _build_train_dataloader_and_compute_training_steps(inner):
                # Actual documented trainer hook for externally supplied data.
                inner.train_dataloader=None;inner.total_training_steps=configuration.max_updates
            def build_models(inner,PolicyWorker,CriticWorker,RefWorker):
                return super().build_models(verified_policy,CriticWorker,RefWorker)

        class SourceExperiment(BasePPOExp):
            def __init__(inner):
                # Replace stock tokenizer/data/generator construction. All model,
                # placement, inference and dispatch construction use upstream hooks.
                inner.cfg=cfg;inner.tokenizer=tokenizer;inner.train_dataset=None;inner.eval_dataset=None
                inner.colocate_pg=inner.get_colocate_pg()
                inner._server_groups=inner._prefill_server_groups=inner._decode_server_groups=inner._inference_router=None
            def get_generator(inner,*args):
                return None  # Trainer.train/eval are unused; actual AgentRunner owns collection.
            def get_trainer(inner,**kwargs): return ExternalTrainer(**kwargs)

        self.experiment=SourceExperiment();self.trainer=self.experiment._setup_trainer()
        self.bridge=SkyRLUpdateBridge(self.trainer)
        client=self.trainer.inference_engine_client
        if not client.server_urls: raise ValueError('Actual native inference worker URLs missing')
        if bool(client.uses_lora_weight_sync)!=settings.lora.enabled:
            raise ValueError('Pinned inference construction differs from the configured LoRA sync mode')
        from skyrl.backends.skyrl_train.inference_servers.remote_inference_client import SKYRL_LORA_ADAPTER_NAME
        self.barrier=PolicyBarrier(tuple(client.server_urls))
        self.backend=SkyRLTokenBackend(tokenizer_directory=Path(settings.tokenizer_directory),
            tokenizer_sha256=settings.tokenizer_sha256,endpoint=client.proxy_url,
            model_name=policy.identity.model,barrier=self.barrier,max_seq_len=settings.max_seq_len,
            adapter_name=SKYRL_LORA_ADAPTER_NAME if settings.lora.enabled else None)
        self.policy=policy
        asyncio.run(self.bridge.initialize_sync())
        self.last_probe=self.synchronize(policy)

    def activate_checkpoint(self,checkpoint,policy):
        """Sequentially load an exact frozen evaluation arm, sync/probe and publish binding.

        Initial arms use their imported native model-directory manifest. Trained
        arms use a real TrainingCheckpoint and its bound external FSDP directory.
        Calling this invalidates all previous arms sharing this session/barrier.
        """
        from datetime import datetime,timezone
        self.registry.assert_usable(checkpoint)
        policy=c.PolicyConfig.model_validate_json(policy.model_dump_json())
        if (policy.identity.model!=self.backend.model_name or policy.identity.provider!='skyrl'
            or policy.identity.tokenizer_digest!=self.backend.tokenizer_digest
            or policy.system_prompt.visibility!=c.Visibility.PUBLIC):
            raise ValueError('Evaluation policy differs from native model/tokenizer/public prompt boundary')
        if checkpoint.kind=='TrainingCheckpoint':
            from .checkpoints import validate_selected_checkpoint
            value=validate_selected_checkpoint(self.store,self.registry,checkpoint)
            if value.weights!=policy.identity.weights or value.policy_version!=policy.policy_version:
                raise ValueError('Arm policy differs from frozen TrainingCheckpoint weights/version')
            progress=[ref for ref in value.provenance.inputs if ref.kind=='m7-checkpoint-progress']
            if len(progress)!=1:raise ValueError('Native checkpoint progress binding missing')
            metadata=decode_json(self.store.get_bytes(progress[0],max_envelope_bytes=8*1024*1024,max_payload_bytes=4*1024*1024),4*1024*1024)
            native=metadata['progress']['native']
            if (native['checkpoint']!=value.optimizer_state.model_dump(mode='json')
                or metadata['settings'].get('lora')!=self.settings.lora.model_dump(mode='json')):
                raise ValueError('Frozen native checkpoint manifest differs')
            self.resume(checkpoint=value.optimizer_state,path=native['path'],policy=policy)
        elif checkpoint==self.configuration.initial_policy.identity.weights and checkpoint==policy.identity.weights:
            require_private_tree(Path(self.settings.model_directory))
            verify_directory(store=self.store,ref=checkpoint,path=Path(self.settings.model_directory))
            if self.policy.identity.weights!=checkpoint:
                self.barrier.begin(self.barrier.stamp,self.barrier.probe)
                self.trainer.dispatch.init_model('policy',self.settings.model_directory,
                    num_training_steps=self.configuration.max_updates)
                self.trainer.global_step=0
            self.last_probe=self.synchronize(policy)
        else:raise ValueError('Exact initial native weights or complete TrainingCheckpoint required')
        config=self.store.put_bytes(canonical_json({'settings':self.settings.model_dump(mode='json'),
            'training':self.configuration.model_dump(mode='json'),'revision':self.revision}),
            'm7-native-configuration',c.Visibility.PRIVATE)
        from feature_rl.registry.core import references
        self.registry.register(config,dependencies=tuple(dict.fromkeys(references(self.configuration.model_dump(mode='json')))))
        probe=self.store.put_bytes(canonical_json(self.last_probe),'m7-native-probe',c.Visibility.PRIVATE)
        self.registry.register(probe,dependencies=(policy.identity.weights,))
        value=ActivationReceipt(checkpoint=checkpoint,policy=policy,configuration=config,probe=probe,
            workers=self.barrier.workers,tokenizer_sha256=self.backend.tokenizer_digest,
            template_sha256=self.backend.template_digest,implementation_revision=self.revision,recorded_at=datetime.now(timezone.utc))
        ref=self.store.put_bytes(canonical_json(value.model_dump(mode='json')),'m7-native-activation',c.Visibility.PRIVATE)
        self.registry.register(ref,dependencies=tuple(dict.fromkeys((checkpoint,policy.identity.weights,policy.system_prompt,config,probe))))
        return ref

    def validate_activation(self,ref,*,checkpoint,policy):
        """Recheck live barrier immediately before use; a historical receipt is insufficient."""
        value=read_activation(self.store,ref);self.registry.assert_usable(ref)
        if (value.checkpoint!=checkpoint or value.policy!=policy or value.workers!=self.barrier.workers
            or value.implementation_revision!=self.revision or value.tokenizer_sha256!=self.backend.tokenizer_digest
            or value.template_sha256!=self.backend.template_digest):raise ValueError('Native arm activation binding differs')
        self.backend.verify_policy(policy)
        return value

    def _ensure_colocated_inference_asleep(self):
        """Pinned weight sync and HF export backload policy without checking vLLM."""
        bridge=getattr(self,'bridge',None)
        if bridge is None:
            raise RuntimeError('native bridge is required before colocated inference residency checks')
        asyncio.run(bridge._ensure_colocated_inference_asleep())

    def synchronize(self,policy):
        """Await native broadcast, then independently probe each actual worker endpoint."""
        if policy.identity.weights is None:raise ValueError('Exact synchronized weights required')
        if (len(self.settings.probe_tokens)+self.settings.probe_output_tokens>self.settings.max_seq_len
            or any(t>=self.backend.vocab_size for t in self.settings.probe_tokens)):
            raise ValueError('Fixed-input probe exceeds native context/vocabulary')
        stamp=PolicyStamp(policy.policy_version,policy.identity.weights.sha256,self.backend.tokenizer_digest,self.backend.template_digest)
        self.barrier.begin(stamp,(0,))  # Revokes all old acknowledgments before any call.
        self._ensure_colocated_inference_asleep()
        asyncio.run(self.trainer.dispatch.save_weights_for_sampler())
        if self.settings.lora.enabled:
            self._validate_adapter(Path(self.settings.work_directory)/'lora-sync')
        results={}
        for endpoint in self.barrier.workers:
            payload={'model':self.backend.inference_model,'token_ids':list(self.settings.probe_tokens),
                'cache_salt':policy.policy_version,'sampling_params':{'n':1,'temperature':0.,'top_p':1.,'top_k':-1,
                'seed':0,'max_tokens':self.settings.probe_output_tokens,'logprobs':0}}
            request=Request(endpoint.rstrip('/')+'/inference/v1/generate',data=canonical_json(payload),
                headers={'Content-Type':'application/json','X-Session-ID':'feature-rl-sync-'+policy.policy_version},method='POST')
            with urlopen(request,timeout=self.settings.probe_timeout_seconds) as response:raw=response.read(1048577)
            value=decode_json(raw,1048576)
            if len(value['choices'])!=1: raise ValueError('Unique fixed-input native probe response required')
            choice=value['choices'][0];tokens=tuple(choice['token_ids'])
            if choice['finish_reason'] not in ('stop','length') or not tokens or len(tokens)>self.settings.probe_output_tokens:
                raise ValueError('Native probe interrupted or malformed')
            if any(type(t) is not int or not 0<=t<self.backend.vocab_size for t in tokens): raise ValueError('Invalid native probe tokens')
            results[endpoint]=tokens
        expected=next(iter(results.values()))
        if any(tokens!=expected for tokens in results.values()): raise ValueError('Synchronized workers disagree on fixed-input generation')
        self.barrier.begin(stamp,expected)
        for endpoint,tokens in results.items(): self.barrier.acknowledge(endpoint,stamp,tokens)
        self.barrier.require(stamp);self.policy=policy
        return {'version':'m7-native-probe-v1','stamp':asdict(stamp),'input':self.settings.probe_tokens,
            'outputs':results,'inference_model':self.backend.inference_model,
            'lora':self.settings.lora.model_dump(mode='json'),'scope':'native_execution',
            'trust':'Controller-owned native broadcast plus endpoint probes; not cryptographic remote tensor attestation'}

    def update(self,rows,*,algorithm):
        from .worker_state import compare_states
        self.backend.verify_policy(self.policy)
        next_step='global_step_'+str(self.trainer.global_step+1)
        if (Path(self.trainer.cfg.trainer.export_path)/next_step).exists() or (Path(self.trainer.cfg.trainer.ckpt_path)/next_step).exists():
            raise ValueError('Native output step already exists; recover retained work instead of overwriting immutable tensors')
        before=self.worker_snapshot()
        self.barrier.begin(self.barrier.stamp,self.barrier.probe)
        status=asyncio.run(self.bridge.update(rows,algorithm=algorithm))
        if status.get('optimizer_skipped'):
            self.last_probe=self.synchronize(self.policy);return status
        status={key:float(value) for key,value in status.items()}
        norm=status.get('grad_norm')
        if type(norm) not in (float,int) or not math.isfinite(norm) or norm<0:
            raise ValueError('Actual finite native optimizer gradient norm missing')
        after=self.worker_snapshot();change=compare_states(before,after)
        # Export backloads policy. The sampler sync above left vLLM awake.
        self._ensure_colocated_inference_asleep()
        self.trainer.save_models()
        path=Path(self.trainer.cfg.trainer.export_path)/('global_step_'+str(self.trainer.global_step))/'policy'
        if self.settings.lora.enabled:self._validate_adapter(path)
        self._write_binding(path)
        weights=publish_directory(store=self.store,registry=self.registry,path=path,
            dependencies=(self.configuration.initial_policy.identity.weights,self.configuration.reference_checkpoint))
        version='step-'+str(self.trainer.global_step)+'-'+weights.sha256[:16]
        identity=self.policy.identity.model_copy(update={'weights':weights})
        policy=self.policy.model_copy(update={'identity':identity,'policy_version':version})
        self.last_probe=self.synchronize(policy)
        return {**status,'weights':weights.model_dump(mode='json'),'policy_version':version,
            'export_path':str(path),'probe':self.last_probe,'before_worker_state':before,
            'after_worker_state':after,**change}

    def worker_snapshot(self):
        """Call our declared method on the actual pinned policy actor handles."""
        import ray
        return ray.get([self.trainer._get_dp_group_models(rank,'policy_model').feature_rl_snapshot.remote()
            for rank in range(self.settings.num_gpus)])

    def save_reload(self):
        """Actual FSDP optimizer/scheduler/model/RNG save + exact-path reload + probe."""
        before=self.last_probe
        before_state=self.worker_snapshot()
        if (Path(self.trainer.cfg.trainer.ckpt_path)/('global_step_'+str(self.trainer.global_step))).exists():
            raise ValueError('Native checkpoint step already exists; retained files are immutable')
        self._ensure_colocated_inference_asleep()
        path=Path(self.trainer.save_checkpoints()).resolve()
        import torch,random,numpy
        torch.save({'python':random.getstate(),'numpy':numpy.random.get_state(),
            'torch':torch.get_rng_state(),'cuda':torch.cuda.get_rng_state_all()},path/'controller-state.pt')
        if self.settings.lora.enabled:self._validate_adapter(path/'policy'/'lora_adapter')
        self._write_binding(path)
        ref=publish_directory(store=self.store,registry=self.registry,path=path,
            dependencies=(self.configuration.initial_policy.identity.weights,self.configuration.reference_checkpoint))
        verify_directory(store=self.store,ref=ref,path=path)
        self._load(path)
        after=self.synchronize(self.policy)
        after_state=self.worker_snapshot()
        if before['outputs']!=after['outputs']: raise ValueError('Checkpoint reload changes fixed-input inference')
        if before_state!=after_state:raise ValueError('Native reload changes actual trainable tensors or optimizer state')
        self.last_probe=after
        return {'checkpoint':ref.model_dump(mode='json'),'path':str(path),'global_step':self.trainer.global_step,
            'before_probe':before,'after_probe':after,'before_worker_state':before_state,'after_worker_state':after_state}

    def _load(self,path):
        require_private_tree(Path(path))
        binding=decode_json((Path(path)/'feature_rl_native.json').read_bytes(),4*1024*1024)
        if binding!=self._native_binding():
            raise ValueError('Native checkpoint base/reference/LoRA/trainable/optimizer topology differs')
        if self.settings.lora.enabled:self._validate_adapter(Path(path)/'policy'/'lora_adapter')
        self.barrier.begin(self.barrier.stamp,self.barrier.probe)
        self.trainer.resume_mode=type(self.trainer.resume_mode)('from_path')
        self.trainer.cfg.trainer.resume_path=str(path)
        step,loaded=self.trainer.load_checkpoints()
        if Path(loaded).resolve()!=Path(path).resolve(): raise ValueError('Native checkpoint selection differs')
        self.trainer.global_step=step
        import torch,random,numpy
        state=torch.load(Path(path)/'controller-state.pt',map_location='cpu',weights_only=False)
        if len(state['cuda'])!=torch.cuda.device_count(): raise ValueError('Controller CUDA RNG topology differs')
        random.setstate(state['python']);numpy.random.set_state(state['numpy'])
        torch.set_rng_state(state['torch']);torch.cuda.set_rng_state_all(state['cuda'])
        if self._native_binding()!=binding:
            raise ValueError('Restored native optimizer or trainable parameter mask differs')
        self.bridge.ready=True

    def _native_binding(self):
        import ray
        workers=ray.get([self.trainer._get_dp_group_models(rank,'policy_model').feature_rl_trainable_binding.remote()
            for rank in range(self.settings.num_gpus)])
        return {'version':'m7-native-tensor-binding-v1',
            'base_weights':self.configuration.initial_policy.identity.weights.model_dump(mode='json'),
            'reference_weights':self.configuration.reference_checkpoint.model_dump(mode='json'),
            'lora':self.settings.lora.model_dump(mode='json'),'num_gpus':self.settings.num_gpus,
            'optimizer_family':OPTIMIZER_FAMILY,'trainable_workers':workers}

    def _write_binding(self,path):
        with (Path(path)/'feature_rl_native.json').open('xb') as stream:
            stream.write(canonical_json(self._native_binding()))

    def _validate_adapter(self,path):
        require_private_tree(Path(path))
        config=decode_json((Path(path)/'adapter_config.json').read_bytes(),1024*1024)
        lora=self.settings.lora
        if (config.get('peft_type')!='LORA' or config.get('r')!=lora.rank
            or config.get('lora_alpha')!=lora.alpha or config.get('lora_dropout')!=lora.dropout
            or config.get('bias','none')!='none' or config.get('modules_to_save')
            or config.get('use_dora',False) or not config.get('target_modules')
            or not (Path(path)/'adapter_model.safetensors').is_file()
            or (Path(path)/'adapter_model.safetensors').stat().st_size==0):
            raise ValueError('Native PEFT export/sync differs from the configured LoRA adapter')

    def resume(self,*,checkpoint,path,policy):
        require_private_tree(Path(path))
        verify_directory(store=self.store,ref=checkpoint,path=Path(path))
        verify_directory(store=self.store,ref=self.configuration.reference_checkpoint,path=Path(self.settings.reference_directory))
        self._load(Path(path));self.last_probe=self.synchronize(policy)
        return self.last_probe

    def close(self):
        """Revoke inference, then close this controller's owned Ray connection."""
        self.barrier.begin(self.barrier.stamp,self.barrier.probe)
        import ray
        ray.shutdown()

# Pinned FSDPStrategy.create_optimizer constructs torch.optim.AdamW directly.
OPTIMIZER_FAMILY = 'adamw'
