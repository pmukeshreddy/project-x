"""Authenticate selected completed training checkpoints through the existing Registry."""
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.registry import Claim
from feature_rl.verifiers.language import decode_json


def _validate_checkpoint(store,registry,ref,*,configuration=None,recovery=False):
    """Return the actual TrainingCheckpoint; callers own roster/evaluation partition gates."""
    registry.assert_usable(ref);checkpoint=store.get_artifact(ref)
    if not isinstance(checkpoint,c.TrainingCheckpoint):raise ValueError('Actual TrainingCheckpoint required')
    if configuration is not None and checkpoint.configuration!=configuration:raise ValueError('Checkpoint differs from frozen training configuration')
    requests=[r for r in checkpoint.provenance.inputs if r.kind=='m7-training-request']
    progress=[r for r in checkpoint.provenance.inputs if r.kind=='m7-checkpoint-progress']
    if len(requests)!=1 or len(progress)!=1:raise ValueError('Exact training request/progress provenance required')
    def read(ref):return decode_json(store.get_bytes(ref,max_envelope_bytes=8*1024*1024,max_payload_bytes=4*1024*1024),4*1024*1024)
    request=read(requests[0]);state=read(progress[0]);position=state['progress']
    claim=Claim.model_validate_json(canonical_json(position['claim']))
    job=registry.job(claim.job_id)
    if recovery:
        if (job.spec.operation!='train' or job.state!='running' or job.spec.configuration!=requests[0]
            or not any(a.claim==claim and a.state=='running' for a in registry.attempts(job.job_id))):
            raise ValueError('Recovery requires the original running training claim')
    elif (job.job_id not in registry.trace(ref).jobs or job.spec.operation!='train' or job.state!='completed'
        or job.spec.configuration!=requests[0] or job.result is None or job.result.disposition!=c.Disposition.SUCCESS
        or tuple(x for x in job.result.artifacts if x.kind=='TrainingCheckpoint')!=(ref,)
        or not any(a.claim==claim and a.state=='completed' for a in registry.attempts(job.job_id))):
        raise ValueError('Checkpoint is not the sole selected output of its completed successful train job')
    config=checkpoint.configuration.model_dump(mode='json')
    if (request['version']!='m7-training-request-v1' or request['configuration']!=config or state['configuration']!=config
        or state['request']!=requests[0].model_dump(mode='json') or state['settings']!=request['settings']
        or position['request']!=requests[0].model_dump(mode='json') or tuple(job.spec.inputs)!=checkpoint.configuration.tasks
        or checkpoint.provenance.producer!='feature_rl.training' or checkpoint.provenance.producer_version!=job.spec.implementation
        or request['revision']!=job.spec.implementation):raise ValueError('Selected training configuration/provenance differs')
    policy=c.PolicyConfig.model_validate_json(canonical_json(position['policy']))
    if (checkpoint.weights!=policy.identity.weights or checkpoint.policy_version!=policy.policy_version
        or checkpoint.optimizer_state.model_dump(mode='json')!=position['native']['checkpoint']
        or checkpoint.data_position!=position['data_position'] or checkpoint.optimizer_steps!=position['optimizer_steps']
        or checkpoint.reference_checkpoint!=checkpoint.configuration.reference_checkpoint
        or [ref.model_dump(mode='json') for ref in checkpoint.consumed_tasks]!=position['consumed']
        or not set(checkpoint.consumed_tasks).issubset(checkpoint.configuration.tasks)):
        raise ValueError('Selected checkpoint tensor/data/progress joins differ')
    if recovery:
        if checkpoint.update_evidence!=checkpoint.reload_evidence or len(checkpoint.update_evidence)!=1:
            raise ValueError('Exact confirmed update/reload evidence required')
        evidence=checkpoint.update_evidence[0]
        if len(evidence.artifacts)!=1:raise ValueError('Unique native update receipt required')
        receipt_ref=evidence.artifacts[0];receipt=read(receipt_ref)
        observations=registry.accounting(job.job_id).observations
        key=claim.attempt_id+':update:'+str(position['updates']+position.get('unknown_updates',0)-1)
        matches=[o for o in observations if o.observation.source=='m7-native-update'
            and o.observation.upstream_attempt_id==key and o.observation.revision==3
            and ref in o.observation.receipts and progress[0] in o.observation.receipts
            and receipt_ref in o.observation.receipts]
        if len(matches)!=1:raise ValueError('Checkpoint lacks its selected confirmed update observation')
        intent_ref=c.ArtifactRef.model_validate_json(canonical_json(receipt['intent']));intent=read(intent_ref)
        if (intent_ref not in matches[0].observation.receipts or intent['request']!=position['request']
            or intent['index']!=position['updates']+position.get('unknown_updates',0)-1 or receipt['version']!='m7-native-update-v1'
            or receipt['reload']!=position['native'] or receipt['status']['weights']!=checkpoint.weights.model_dump(mode='json')):
            raise ValueError('Confirmed update/intent/reload differs from recovery checkpoint')
        later=[o for o in observations if o.observation.source=='m7-native-update'
            and int(o.observation.upstream_attempt_id.rsplit(':',1)[1])>=position['updates']+position.get('unknown_updates',0)]
        if any(o.observation.revision!=1 for o in later):raise ValueError('Recovery checkpoint is older than a confirmed update')
    return checkpoint


def validate_selected_checkpoint(store,registry,ref,*,configuration:c.TrainingConfig|None=None):
    """Evaluation admission: only the sole completed successful selected output."""
    return _validate_checkpoint(store,registry,ref,configuration=configuration)


def validate_recovery_checkpoint(store,registry,ref,*,configuration:c.TrainingConfig):
    """Recovery only: confirmed update of a still-running job; never evaluation admission."""
    return _validate_checkpoint(store,registry,ref,configuration=configuration,recovery=True)
