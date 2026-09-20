"""Durable synchronous feature SFT/GRPO service over actual released tasks and M3/M4.

Sampling assignments are written before dispatch. Lost updates are never replayed
implicitly. Explicit verified checkpoints restore native optimizer/RNG and the
frozen sampler/signal/data cursor; unknown costs remain unknown.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import math
import fcntl
import os
from pathlib import Path
import time
from typing import Annotated,Literal,Union
from pydantic import Field,TypeAdapter
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.agents import AgentRunner
from feature_rl.agents.runner import aggregate,cost
from feature_rl.registry import JobSpec,CostObservation
from feature_rl.registry.core import references
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_local
from .core import BalancedSampler,TaskSlot,GroupPlan,SignalGate
from .data import TrainingDataGate
from .native import NativeSession,NativeSettings,require_private_tree
from .factory import NativeSessionFactory
from .skyrl_bridge import group_rows,UpdateRow
from .supervision import DemonstrationImporter


class SourceDemonstration(c.StrictModel):
    kind: Literal['source']='source'
    task: c.ArtifactRef
    submission: c.ArtifactRef
    grade: c.ArtifactRef

class TrajectoryDemonstration(c.StrictModel):
    kind: Literal['trajectory']='trajectory'
    rollout: c.ArtifactRef

Demonstration=Annotated[Union[SourceDemonstration,TrajectoryDemonstration],Field(discriminator='kind')]
_DEMOS=TypeAdapter(tuple[Demonstration,...])


class TrainingRecoveryRequired(RuntimeError):
    def __init__(self,message,*,job_id,journal):
        super().__init__(message);self.job_id=job_id;self.journal=journal


class TrainingService:
    def __init__(self,*,store,registry,lifecycle,builder,runtime,grader,settings:NativeSettings,
                 revision:str,evidence_scope:Literal['real_integration','unit_diagnostic']='real_integration'):
        from feature_rl.artifacts import ArtifactStore
        from feature_rl.registry import Registry
        from feature_rl.pipeline import TaskLifecycle,ReleasedTaskResolver,TaskBuilder
        from feature_rl.environments import EnvironmentRuntime
        from feature_rl.grading import GradingService
        if (type(store) is not ArtifactStore or store.role!=c.ActorRole.CONTROLLER
            or type(registry) is not Registry or registry.store is not store
            or type(lifecycle) not in (TaskLifecycle,ReleasedTaskResolver) or lifecycle.store is not store or lifecycle.registry is not registry
            or type(builder) is not TaskBuilder or builder.store is not store or builder.registry is not registry
            or type(runtime) is not EnvironmentRuntime or runtime.store is not store
            or type(grader) is not GradingService or grader.store is not store or grader.runtime is not runtime):
            raise TypeError('Actual same-store M0/M3/M4/M6 services required')
        self.store,self.registry,self.lifecycle,self.builder,self.runtime,self.grader=store,registry,lifecycle,builder,runtime,grader
        self.settings=NativeSettings.model_validate_json(settings.model_dump_json())
        if len(revision) not in (40,64) or any(x not in '0123456789abcdef' for x in revision):raise ValueError('Exact training revision required')
        if evidence_scope not in ('real_integration','unit_diagnostic'):raise ValueError('Explicit training evidence scope required')
        self.revision,self.scope=revision,evidence_scope

    def _put(self,value,kind,dependencies=()):
        payload=canonical_json(value.model_dump(mode='json') if hasattr(value,'model_dump') else value)
        if len(payload)>4*1024*1024:raise ValueError('Training receipt byte bound')
        ref=self.store.put_bytes(payload,kind,c.Visibility.PRIVATE)
        self.registry.register(ref,dependencies=tuple(dict.fromkeys(dependencies)))
        return ref

    def _observe(self,source,key,revision,refs,costs):
        return self.registry.reconcile(self.claim,CostObservation(source=source,upstream_attempt_id=key,
            revision=revision,receipts=tuple(refs),costs=aggregate(costs)))

    def _costs(self):
        rows=sorted(self.registry.accounting(self.claim.job_id).observations,key=lambda r:r.observation_id)
        return tuple(v for row in rows for v in row.observation.costs),tuple(row.observation_id for row in rows)

    def _write(self):
        self.state['elapsed_wall']=self._elapsed_base+time.monotonic()-self._started
        payload=canonical_json(self.state)
        if len(payload)>4*1024*1024:raise ValueError('Training journal byte bound')
        temporary=self.journal.with_suffix('.new')
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.journal)
        directory=os.open(self.journal.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

    def _evidence(self,ref,operation,*,success=True):
        return c.EvidenceRecord(producer='feature_rl.training',command=(operation,self.claim.job_id),
            recorded_at=datetime.now(timezone.utc),exit_status=0 if success else 1,
            artifacts=(ref,),revision=self.revision,scope=self.scope)

    def _finish(self,disposition,reason):
        self.state['phase']='finalizing';self.state['final_outcome']={'disposition':disposition.value,'reason':reason};self._write()
        if getattr(self,'_live',None) is not None:
            factory,handle=self._live
            factory.close(handle,self.claim,shutdown_key='training-close-'+self.claim.attempt_id)
            self._live=None
        summary=self._put({'version':'m7-training-summary-v1','job_id':self.claim.job_id,'reason':reason,
            'progress':self.state,'unknown_costs_are_zero':False},'m7-training-summary',
            dependencies=(self.request,*tuple(c.ArtifactRef.model_validate_json(canonical_json(v)) for v in self.state['group_receipts']),
                *(() if self.state['checkpoint'] is None else (c.ArtifactRef.model_validate_json(canonical_json(self.state['checkpoint'])),))))
        costs,observations=self._costs()
        artifacts=(summary,) if self.state['checkpoint'] is None else (c.ArtifactRef.model_validate_json(canonical_json(self.state['checkpoint'])),summary)
        result=c.OperationResult(operation='train',disposition=disposition,artifacts=artifacts,
            evidence=(self._evidence(summary,'TrainingService.train',success=disposition==c.Disposition.SUCCESS),),costs=costs,reason=reason)
        self.state['frozen_result']=result.model_dump(mode='json');self.state['frozen_observations']=observations
        self.state['phase']='frozen';self._write()
        self.registry.complete(self.claim,result,observations=observations)
        return result

    def train(self,configuration:c.TrainingConfig,*,invocation:str,resume:c.ArtifactRef|None=None,demonstrations=()):
        root=Path(self.settings.work_directory);root.mkdir(mode=0o700,parents=True,exist_ok=True)
        require_private_tree(root)
        descriptor=os.open(root/'.controller.lock',os.O_CREAT|os.O_RDWR,0o600)
        with os.fdopen(descriptor,'a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise RuntimeError('Native work directory already has an active controller') from exc
            try:return self._train(configuration,invocation=invocation,resume=resume,demonstrations=demonstrations)
            except BaseException as error:
                if getattr(self,'_live',None) is not None:
                    factory,handle=self._live
                    try:
                        factory.close(handle,self._live_claim,shutdown_key='training-failure-close-'+self._live_claim.attempt_id)
                        self._live=None
                    except BaseException as cleanup:
                        error.native_cleanup_error=cleanup
                        if getattr(cleanup,'native_session_closed',False):self._live=None
                raise
            finally:fcntl.flock(lock,fcntl.LOCK_UN)

    def _train(self,configuration:c.TrainingConfig,*,invocation:str,resume:c.ArtifactRef|None=None,demonstrations=()):
        configuration=c.TrainingConfig.model_validate_json(configuration.model_dump_json())
        demos=_DEMOS.validate_json(canonical_json([d.model_dump(mode='json') for d in demonstrations]))
        if configuration.group_size!=4:raise ValueError('Exactly four assigned GRPO episodes required')
        if configuration.algorithm=='grpo' and demos:raise ValueError('Teacher/reference demonstrations are exclusive to SFT')
        if configuration.algorithm=='sft' and not demos:raise ValueError('SFT requires explicitly supplied verified demonstrations')
        if configuration.budget_usd is not None:
            raise ValueError('A finite USD cap requires an actual native cost meter; current local resource receipts have unknown USD and cannot authorize spend')
        if len(configuration.seeds.seeds)!=1 or len(set(configuration.tasks))!=len(configuration.tasks):raise ValueError('Frozen nonduplicate task roster and one training seed per invocation required')
        # Resolve admission before native construction or any model operation.
        admitted={ref.sha256:self.lifecycle.resolve_released(ref) for ref in configuration.tasks}
        if any(task.partition!=c.Partition.TRAIN for task in admitted.values()):raise ValueError('Training-partition tasks only')
        self.config=configuration
        native_factory=NativeSessionFactory(store=self.store,registry=self.registry,settings=self.settings,
            configuration=configuration,revision=self.revision)
        self.request=self._put({'version':'m7-training-request-v1','configuration':configuration.model_dump(mode='json'),
            'settings':self.settings.model_dump(mode='json'),'demonstrations':[d.model_dump(mode='json') for d in demos],
            'resume':resume.model_dump(mode='json') if resume else None,'lifecycle':self.lifecycle.configuration.model_dump(mode='json'),
            'builder_revision':self.builder.revision,'runtime_revision':self.runtime.revision,'grader_revision':self.grader.revision,
            'revision':self.revision,'scope':self.scope,'native_factory':native_factory.configuration.model_dump(mode='json')},'m7-training-request',
            dependencies=tuple(dict.fromkeys((*references(configuration.model_dump(mode='json')),
                *references([d.model_dump(mode='json') for d in demos]),self.lifecycle.configuration,native_factory.configuration,*(() if resume is None else (resume,))))))
        job=self.registry.enqueue(JobSpec(operation='train',inputs=configuration.tasks,configuration=self.request,
            implementation=self.revision,invocation=invocation,attempt_limit=1))
        if job.state=='completed':return job.result
        if getattr(self,'_live',None) is not None and self._live_claim.job_id!=job.job_id:
            raise ValueError('Retained native session belongs to another selected training job; recover or close that job first')
        if getattr(self,'_opening',None) is not None and self._opening[0].job_id!=job.job_id:
            raise ValueError('Retained native startup belongs to another selected training job')
        self.claim=self.registry.claim(job.job_id,owner='feature_rl.training',claim_key='train-'+job.job_id)
        directory=Path(self.settings.work_directory)/'controller';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        if directory.is_symlink():raise ValueError('Private controller journal directory required')
        self.journal=directory/(job.job_id+'.json')
        sampler=BalancedSampler([TaskSlot(ref.sha256,admitted[ref.sha256].repository_family,
            admitted[ref.sha256].request_lineage[0]) for ref in configuration.tasks],seed=configuration.seeds.seeds[0])
        signal=SignalGate()
        if self.journal.exists():
            self.state=decode_json(self.journal.read_bytes(),4*1024*1024)
            if self.state['request']!=self.request.model_dump(mode='json') or self.state['claim']!=self.claim.model_dump(mode='json'):
                raise ValueError('Training journal request/claim differs')
            if self.state['phase']=='frozen':
                result=c.OperationResult.model_validate_json(canonical_json(self.state['frozen_result']))
                self.registry.complete(self.claim,result,observations=tuple(self.state['frozen_observations']));return result
            if self.state['phase']=='finalizing':
                self._elapsed_base=self.state['elapsed_wall'];self._started=time.monotonic()
                outcome=self.state['final_outcome'];return self._finish(c.Disposition(outcome['disposition']),outcome['reason'])
            retained_open=(getattr(self,'_opening',None) is not None and self._opening[0]==self.claim)
            if self.state['phase'] not in ('collecting','ready') and not (self.state['phase']=='initializing' and retained_open):
                raise TrainingRecoveryRequired('Unconfirmed native phase: reconcile retained work or explicitly resume a verified checkpoint; never replay update',job_id=job.job_id,journal=self.journal)
        else:
            self.state={'version':'m7-training-progress-v1','request':self.request.model_dump(mode='json'),
                'claim':self.claim.model_dump(mode='json'),'phase':'initializing','elapsed_wall':0.,'sampler':sampler.state_dict(),
                'signal':{},'pending':None,'unclassified_group':None,'batch_receipts':[],'supervised_positions':[],
                'groups':0,'updates':0,'optimizer_steps':0,'data_position':0,
                'group_receipts':[],'consumed':[],'checkpoint':None,'native':None,'nonzero_updates':0,
                'policy':configuration.initial_policy.model_dump(mode='json')}
            if resume is not None:self._restore_progress(resume)
        self._elapsed_base=self.state['elapsed_wall'];self._started=time.monotonic();self._write()
        self._observe('m7-training-controller',self.claim.attempt_id,1,(self.request,),
            (cost('training',note='Native/controller resource costs are unknown until each phase receipt'),
             cost('storage',note='Private native tensor/checkpoint and CAS costs remain unmeasured')))
        if getattr(self,'_live',None) is None:
            if getattr(self,'_opening',None) is None:
                count=len(self.registry.accounting(job.job_id).observations)
                startup_key=self.claim.attempt_id+':native-init:'+str(count)
                self.state['phase']='initializing';self.state['startup_key']=startup_key;self._write()
                self._opening=(self.claim,native_factory,startup_key)
            _,native_factory,startup_key=self._opening
            handle=native_factory.create(self.claim,startup_key=startup_key)
            self._live=(native_factory,handle)
            self._live_claim=self.claim;self._opening=None
        self.native=self._live[1].session
        if self.state['native'] is not None:
            native=self.state['native'];policy=c.PolicyConfig.model_validate_json(canonical_json(self.state['policy']))
            self.native.resume(checkpoint=c.ArtifactRef.model_validate_json(canonical_json(native['checkpoint'])),path=native['path'],policy=policy)
        self.runner=AgentRunner(store=self.store,registry=self.registry,lifecycle=self.lifecycle,builder=self.builder,
            runtime=self.runtime,grader=self.grader,backend=self.native.backend,revision=self.revision,evidence_scope=self.scope)
        self.gate=TrainingDataGate(store=self.store,admit=self.lifecycle.resolve_released,grader_revision=self.grader.revision,runner=self.runner)
        sampler.load_state_dict(self.state['sampler']);signal.load_state_dict(self.state['signal'])
        self.state['phase']='collecting';self._write()
        examples=[]
        if configuration.algorithm=='sft':
            importer=DemonstrationImporter(self.runner)
            for demo in demos:
                example=(importer.source(task=demo.task,submission=demo.submission,grade=demo.grade,policy=self.native.policy)
                    if isinstance(demo,SourceDemonstration) else importer.trajectory(demo.rollout,policy=self.native.policy))
                if example.task not in configuration.tasks:raise ValueError('Demonstration outside frozen training roster')
                examples.append(example)
        while self.state['updates']<configuration.max_updates:
            if self._elapsed_base+time.monotonic()-self._started>=self.settings.max_wall_seconds:
                return self._finish(c.Disposition.BLOCKED,'Declared controller wall budget exhausted between bounded phases')
            if self.state['groups']>=self.settings.max_groups and not (self.state['batch_receipts'] or self.state['supervised_positions'] or self.state['unclassified_group']):
                return self._finish(c.Disposition.BLOCKED,'Declared assigned-group budget exhausted; no replacement samples')
            if configuration.algorithm=='grpo':
                groups=[self._prepared_from_ref(c.ArtifactRef.model_validate_json(canonical_json(value))) for value in self.state['batch_receipts']]
                available=self.settings.max_groups-self.state['groups']+int(self.state['unclassified_group'] is not None)
                for _ in range(min(self.settings.groups_per_update-len(groups),available)):
                    group=self._collect(sampler)
                    if not signal.ready:
                        signal.observe(group.plan.group_id,group.plan.task.task_id,group.rewards)
                        self.state['signal']=signal.state_dict()
                        if not signal.ready:
                            self.state['unclassified_group']=None;self._write()
                            if len(signal.state_dict())==64:
                                return self._finish(c.Disposition.BLOCKED,'Real feasibility probe exhausted64groups without8mixed groups spanning4training tasks')
                            continue
                    groups.append(group)
                    self.state['batch_receipts'].append(self.state['unclassified_group'])
                    self.state['unclassified_group']=None;self._write()
                rows=group_rows(groups)
            else:
                rows=[]
                for _ in range(min(self.settings.groups_per_update-len(self.state['supervised_positions']),self.settings.max_groups-self.state['groups'])):
                    position=self.state['data_position'];example=examples[position%len(examples)]
                    self.gate.admit_task(example.task)
                    self.state['supervised_positions'].append(position)
                    self.state['data_position']+=1;self.state['groups']+=1
                    if example.task.model_dump(mode='json') not in self.state['consumed']:self.state['consumed'].append(example.task.model_dump(mode='json'))
                self._write()
                for position in self.state['supervised_positions']:
                    example=examples[position%len(examples)]
                    for n,turn in enumerate(example.turns):rows.append(UpdateRow(turn,'sft-'+str(position),0,n==len(example.turns)-1,1.))
            if not rows:
                self.state['batch_receipts']=[];self._write();continue
            self._update(rows)
        if self.state['nonzero_updates']<1 or self.state['checkpoint'] is None:
            return self._finish(c.Disposition.BLOCKED,'No actual nonzero-gradient changed-weight update with verified native reload')
        return self._finish(c.Disposition.SUCCESS,'Native supervised/GRPO updates, synchronized inference and exact checkpoint reload completed')

    def _collect(self,sampler):
        if self.state['unclassified_group'] is not None:
            return self._prepared_from_ref(c.ArtifactRef.model_validate_json(canonical_json(self.state['unclassified_group'])))
        if self.state['pending'] is None:
            plan=sampler.next_group(self.native.policy.policy_version)
            self.state['pending']={'plan':asdict(plan),'records':[]}
            self.state['sampler']=sampler.state_dict();self._write()
        pending=self.state['pending'];value=pending['plan']
        plan=GroupPlan(value['group_id'],TaskSlot(**value['task']),value['policy_version'],tuple(value['episode_seeds']),value['case_seed'],value['position'])
        if plan.policy_version!=self.native.policy.policy_version:raise ValueError('Pending group is stale relative to synchronized checkpoint')
        task=next(ref for ref in self.config.tasks if ref.sha256==plan.task.task_id)
        for index in range(len(pending['records']),4):
            policy=self.native.policy.model_copy(update={'seed':plan.episode_seeds[index]})
            result=self.runner.run(task,policy,self.config.limits,case_seed=plan.case_seed,
                invocation='train-'+self.claim.job_id[:16]+'-'+plan.group_id+'-'+str(index))
            refs=[ref for ref in result.artifacts if ref.kind=='RolloutRecord']
            if len(refs)!=1:raise ValueError('One selected actual rollout required')
            record=self.store.get_artifact(refs[0]);self.runner.validate_record(record)
            self._observe('m7-assigned-run',record.run_id,1,(refs[0],),result.costs)
            pending['records'].append(refs[0].model_dump(mode='json'));self._write()
        refs=tuple(c.ArtifactRef.model_validate_json(canonical_json(v)) for v in pending['records'])
        records=tuple(self.store.get_artifact(ref) for ref in refs)
        contexts=tuple(tuple(step.token_trace.context_token_ids for step in record.steps) if record.reward is not None else () for record in records)
        group=self.gate.prepare_group(plan,records,contexts=contexts,expected_policy=self.native.policy,
            vocab_size=self.native.backend.vocab_size,max_seq_len=self.settings.max_seq_len)
        receipt=self._put({'version':'m7-assigned-group-v1','plan':asdict(plan),'records':pending['records'],
            'rewards':group.rewards,'advantages':group.advantages,'effective_size':group.effective_size},'m7-assigned-group',refs)
        self.state['group_receipts'].append(receipt.model_dump(mode='json'));self.state['groups']+=1
        self.state['unclassified_group']=receipt.model_dump(mode='json')
        self.state['data_position']=sampler.position;self.state['pending']=None
        if task.model_dump(mode='json') not in self.state['consumed']:self.state['consumed'].append(task.model_dump(mode='json'))
        self._write();return group

    def _prepared_from_ref(self,ref):
        self.registry.assert_usable(ref)
        value=decode_json(self.store.get_bytes(ref,max_envelope_bytes=1024*1024,max_payload_bytes=512*1024),512*1024)
        plan=value['plan'];plan=GroupPlan(plan['group_id'],TaskSlot(**plan['task']),plan['policy_version'],
            tuple(plan['episode_seeds']),plan['case_seed'],plan['position'])
        records=tuple(self.store.get_artifact(c.ArtifactRef.model_validate_json(canonical_json(ref))) for ref in value['records'])
        contexts=tuple(tuple(step.token_trace.context_token_ids for step in record.steps) if record.reward is not None else () for record in records)
        return self.gate.prepare_group(plan,records,contexts=contexts,expected_policy=self.native.policy,
            vocab_size=self.native.backend.vocab_size,max_seq_len=self.settings.max_seq_len)

    def _update(self,rows):
        for ref in self.state['consumed']:self.gate.admit_task(c.ArtifactRef.model_validate_json(canonical_json(ref)))
        before=self.native.policy.identity.weights
        self.state['phase']='updating';self._write()
        index=self.state['updates'];key=self.claim.attempt_id+':update:'+str(index)
        intent=self._put({'version':'m7-update-intent-v1','request':self.request.model_dump(mode='json'),
            'index':index,'policy':self.native.policy.model_dump(mode='json'),
            'rows':[{'context':r.turn.context,'targets':r.turn.targets,'mask':r.turn.mask,
                'behavior':r.turn.behavior,'advantage':r.turn.advantage,'instance':r.instance_id,'repetition':r.repetition_id} for r in rows]},
            'm7-update-intent',(self.request,before))
        self._observe('m7-native-update',key,1,(intent,),(cost('training',note='Native optimizer update dispatched; completion/cost unknown'),))
        started=time.monotonic();status=self.native.update(rows,algorithm=self.config.algorithm)
        reload=self.native.save_reload()
        for ref in self.state['consumed']:self.gate.admit_task(c.ArtifactRef.model_validate_json(canonical_json(ref)))
        norm=status.get('grad_norm');changed=status.get('changed_trainable_shards',0)>0 and status.get('changed_optimizer_shards',0)>0
        nonzero=type(norm) in (int,float) and math.isfinite(norm) and norm>0 and changed
        steps=math.ceil(len({r.instance_id for r in rows})/self.settings.mini_batch_groups)
        self.state['updates']+=1;self.state['optimizer_steps']+=steps
        self.state['nonzero_updates']+=int(nonzero);self.state['native']=reload
        self.state['policy']=self.native.policy.model_dump(mode='json')
        self.state['batch_receipts']=[]
        self.state['supervised_positions']=[]
        receipt=self._put({'version':'m7-native-update-v1','intent':intent.model_dump(mode='json'),
            'status':status,'reload':reload,'nonzero_gradient_and_changed_manifest':nonzero,
            'optimizer_steps':steps,'native_global_step':self.native.trainer.global_step},'m7-native-update',
            tuple(dict.fromkeys((intent,*references(status),*references(reload)))))
        self._observe('m7-native-update',key,2,(intent,receipt),(cost('training',wall=time.monotonic()-started,
            note='Actual update/export/broadcast/probes/native save and reload wall; CPU/GPU/currency remain unmeasured'),))
        progress=self._put({'version':'m7-checkpoint-progress-v1','configuration':self.config.model_dump(mode='json'),
            'settings':self.settings.model_dump(mode='json'),'progress':self.state,'request':self.request.model_dump(mode='json')},
            'm7-checkpoint-progress',tuple(dict.fromkeys((self.request,receipt,*references(self.state)))))
        if self.state['nonzero_updates']:
            evidence=self._evidence(receipt,'NativeSession.update-save-reload')
            costs,_=self._costs();optimizer=c.ArtifactRef.model_validate_json(canonical_json(reload['checkpoint']))
            checkpoint=c.TrainingCheckpoint(kind='TrainingCheckpoint',schema_version=1,visibility=c.Visibility.PRIVATE,
                provenance=c.Provenance(producer='feature_rl.training',producer_version=self.revision,created_at=evidence.recorded_at,
                    inputs=(self.request,progress),evidence=(evidence,)),costs=costs,
                weights=self.native.policy.identity.weights,optimizer_state=optimizer,reference_checkpoint=self.config.reference_checkpoint,
                data_position=self.state['data_position'],policy_version=self.native.policy.policy_version,configuration=self.config,
                consumed_tasks=tuple(c.ArtifactRef.model_validate_json(canonical_json(v)) for v in self.state['consumed']),
                optimizer_steps=self.state['optimizer_steps'],update_evidence=(evidence,),reload_evidence=(evidence,))
            ref=self.store.put_artifact(checkpoint);self.registry.register(ref)
            self.state['checkpoint']=ref.model_dump(mode='json')
        self.state['phase']='collecting';self._write()

    def _restore_progress(self,ref):
        from .checkpoints import validate_selected_checkpoint
        checkpoint=validate_selected_checkpoint(self.store,self.registry,ref,configuration=self.config)
        refs=[x for x in checkpoint.provenance.inputs if x.kind=='m7-checkpoint-progress']
        if len(refs)!=1:raise ValueError('Unique frozen sampler/signal/native progress required')
        value=decode_json(self.store.get_bytes(refs[0],max_envelope_bytes=8*1024*1024,max_payload_bytes=4*1024*1024),4*1024*1024)
        if value['settings']!=self.settings.model_dump(mode='json') or value['configuration']!=self.config.model_dump(mode='json'):
            raise ValueError('Native resume settings or frozen task configuration changed')
        restored=value['progress'];policy=c.PolicyConfig.model_validate_json(canonical_json(restored['policy']))
        if (checkpoint.weights!=policy.identity.weights or checkpoint.policy_version!=policy.policy_version
            or checkpoint.optimizer_steps!=restored['optimizer_steps'] or checkpoint.data_position!=restored['data_position']
            or checkpoint.optimizer_state.model_dump(mode='json')!=restored['native']['checkpoint']):raise ValueError('Checkpoint/progress joins differ')
        identity=(self.state['request'],self.state['claim'])
        self.state=restored;self.state.update(request=identity[0],claim=identity[1],checkpoint=ref.model_dump(mode='json'),phase='initializing')
