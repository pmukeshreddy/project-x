"""Inert native configuration; startup/shutdown occur inside an existing selected job."""
from dataclasses import dataclass
from datetime import datetime,timezone
import sys
import time
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.registry import Registry,CostObservation
from feature_rl.registry.core import references
from feature_rl.agents.runner import cost
from .native import NativeSettings,NativeSession


class NativeStartupRecoveryRequired(RuntimeError):pass


@dataclass(frozen=True)
class NativeHandle:
    session: NativeSession
    receipt: c.ArtifactRef
    observation_id: str
    ready_at: float


class NativeSessionFactory:
    """One explicit bootstrap TrainingConfig also serves frozen evaluation arms.

    The caller includes `configuration` in its selected job's dependency closure
    before create(). No CUDA/model operation occurs in this constructor.
    """
    def __init__(self,*,store,registry,settings:NativeSettings,configuration:c.TrainingConfig,revision:str):
        if type(store) is not ArtifactStore or store.role!=c.ActorRole.CONTROLLER or type(registry) is not Registry or registry.store is not store:
            raise TypeError('Actual controller store and Registry required')
        self.store,self.registry=store,registry
        self.settings=NativeSettings.model_validate_json(settings.model_dump_json())
        self.training_configuration=c.TrainingConfig.model_validate_json(configuration.model_dump_json())
        if len(revision) not in (40,64) or any(x not in '0123456789abcdef' for x in revision):raise ValueError('Exact native factory revision required')
        self.revision=revision;self._sessions={};self._pending={};self._closing={};self._closed={}
        payload={'settings':self.settings.model_dump(mode='json'),'training':self.training_configuration.model_dump(mode='json'),'revision':revision}
        self.configuration=store.put_bytes(canonical_json(payload),'m7-native-configuration',c.Visibility.PRIVATE)
        registry.register(self.configuration,dependencies=tuple(dict.fromkeys(references(configuration.model_dump(mode='json')))))

    def _claim(self,claim):
        job=self.registry.job(claim.job_id)
        if job.spec.operation not in ('run','train','evaluate') or job.state!='running':raise ValueError('Selected running run/train/evaluate claim required')
        if not any(a.claim==claim and a.state=='running' for a in self.registry.attempts(job.job_id)):
            raise ValueError('Actual current startup claim required')
        if job.job_id not in self.registry.trace(self.configuration).jobs:
            raise ValueError('Selected job must freeze the native factory configuration dependency')
        self.registry.assert_usable(self.configuration)
        return {'run':'rollout','train':'training','evaluate':'evaluation'}[job.spec.operation]

    def create(self,claim,*,startup_key:str) -> NativeHandle:
        category=self._claim(claim);key=(claim.attempt_id,startup_key)
        if key in self._sessions:return self._sessions[key]
        if key in self._pending:return self._finish_startup(claim,startup_key,category)
        if any(x.observation.source=='m7-native-startup' and x.observation.upstream_attempt_id==startup_key
            for x in self.registry.accounting(claim.job_id).observations):
            raise NativeStartupRecoveryRequired('Startup key already dispatched without a live retained session; preserve prior cost and explicitly assign a new initialization key')
        self.registry.reconcile(claim,CostObservation(source='m7-native-startup',upstream_attempt_id=startup_key,
            revision=1,receipts=(self.configuration,),costs=(cost(category,note='Native initialization dispatched; CPU/GPU/USD and outcome unknown'),)))
        started=time.monotonic();preexisting=sys.modules.get('ray')
        owned=preexisting is None or not preexisting.is_initialized()
        try:
            session=NativeSession(store=self.store,registry=self.registry,settings=self.settings,
                configuration=self.training_configuration,revision=self.revision)
        except BaseException:
            # Only this controller's newly created Ray connection is closed.
            module=sys.modules.get('ray')
            if owned and module is not None and module.is_initialized():module.shutdown()
            raise
        self._pending[key]={'session':session,'started':started,'completed':time.monotonic(),'receipt':None,
            'recorded_at':datetime.now(timezone.utc).isoformat()}
        return self._finish_startup(claim,startup_key,category)

    def _finish_startup(self,claim,startup_key,category):
        key=(claim.attempt_id,startup_key);pending=self._pending[key];session=pending['session']
        if pending['receipt'] is None:pending['receipt']=self.store.put_bytes(canonical_json({'version':'m7-native-startup-v1','claim':claim.model_dump(mode='json'),
            'configuration':self.configuration.model_dump(mode='json'),'startup_key':startup_key,'probe':session.last_probe,
            'recorded_at':pending['recorded_at']}),'m7-native-startup',c.Visibility.PRIVATE)
        receipt=pending['receipt']
        self.registry.register(receipt,dependencies=(self.configuration,))
        observed=self.registry.reconcile(claim,CostObservation(source='m7-native-startup',upstream_attempt_id=startup_key,
            revision=2,receipts=(self.configuration,receipt),costs=(cost(category,wall=pending['completed']-pending['started'],
                note='Actual native initialization and first synchronization/probe wall; CPU/GPU/USD unknown'),)))
        handle=NativeHandle(session,receipt,observed.observation_id,pending['completed']);self._sessions[key]=handle
        del self._pending[key]
        return handle

    def close(self,handle:NativeHandle,claim,*,shutdown_key:str):
        category=self._claim(claim)
        identity=(claim.attempt_id,handle.receipt.sha256,shutdown_key)
        if identity in self._closed:return self._closed[identity]
        keys=[key for key,value in self._sessions.items() if value is handle and key[0]==claim.attempt_id]
        if len(keys)!=1:raise ValueError('Retained session from this factory/claim required')
        if identity not in self._closing:
            self.registry.reconcile(claim,CostObservation(source='m7-native-shutdown',upstream_attempt_id=shutdown_key,
                revision=1,receipts=(handle.receipt,),costs=(cost(category,note='Owned native session shutdown dispatched; outcome/cost unknown'),)))
            self._closing[identity]={'started':time.monotonic(),'completed':None,'receipt':None}
        pending=self._closing[identity]
        if pending['completed'] is None:handle.session.close();pending['completed']=time.monotonic()
        if pending['receipt'] is None:pending['receipt']=self.store.put_bytes(canonical_json({'version':'m7-native-shutdown-v1','startup':handle.receipt.model_dump(mode='json'),
            'native_session_wall_seconds':pending['completed']-handle.ready_at,
            'note':'Session interval includes activation/idle/assigned work; not added again to phase wall costs. GPU/currency unknown.'}),
            'm7-native-shutdown',c.Visibility.PRIVATE)
        receipt=pending['receipt']
        self.registry.register(receipt,dependencies=(handle.receipt,))
        observed=self.registry.reconcile(claim,CostObservation(source='m7-native-shutdown',upstream_attempt_id=shutdown_key,
            revision=2,receipts=(handle.receipt,receipt),costs=(cost(category,wall=pending['completed']-pending['started'],
                note='Actual owned Ray shutdown wall; CPU/GPU/USD unknown'),)))
        del self._sessions[keys[0]]
        del self._closing[identity]
        self._closed[identity]=(receipt,observed.observation_id)
        return self._closed[identity]
