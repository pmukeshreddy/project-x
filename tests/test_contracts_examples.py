"""Complete synthetic examples exercise the public JSON API, not empirical gates."""
import json
from copy import deepcopy
import pytest
from pydantic import ValidationError
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactIntegrityError
from test_contracts import cost, evidence, ref, ARTIFACT_KINDS


def examples():
    def r(kind, visibility='private', encoding='json'):
        return ref(kind,visibility) | {'encoding':encoding,'schema_version':2 if kind in {'CandidateRecord','SourcePair'} else 1}
    raw=ref(); public=ref(visibility='public')
    ev=evidence(); costs=[cost()]
    prov=dict(producer='unit fixture',producer_version='1',created_at='2026-09-19T00:00:00Z',inputs=[],evidence=[ev])
    common=dict(schema_version=1,visibility='private',provenance=prov,costs=costs)
    limits=dict(wall_seconds=30.0,cpu_seconds=15.0,memory_bytes=1024,pids=10,output_bytes=1024,disk_bytes=4096,tool_calls=10,input_tokens=100,output_tokens=100)
    seeds=dict(algorithm='python-random-v1',seeds=[13],same_cases_within_group=True)
    relation=dict(integration='merge',target_before='a'*40,integrated_after='b'*40,implementation_commits=['b'*40],parents=['a'*40],evidence=[ev])
    link=dict(source=raw,locator='issue body',quote='Add suggestions',provenance_label='historical_request')
    req=dict(requirement_id='R1',statement='Add suggestions',mandatory=True,evidence=[link],observable='CLI error output')
    allowed=dict(source_roots=['src/'],forbidden_paths=['.git/'],dependencies='forbidden',dependency_artifacts=[],additional_artifact_types=[])
    command=dict(argv=['python','--version'],working_directory='/workspace',timeout_seconds=5.0)
    identity=dict(provider='unit',model='fixture',revision='1',weights=None,tokenizer_digest='c'*64)
    policy=dict(identity=identity,policy_version='p1',temperature=1.0,top_p=1.0,seed=13,system_prompt=public,harness_version='1',require_token_probabilities=True)
    training=dict(initial_policy=policy,reference_checkpoint=raw,tasks=[r('TaskBundle')],limits=limits,seeds=seeds,algorithm='grpo',group_size=4,max_updates=1,learning_rate=0.0001,framework='unit fixture',framework_version='1',backend_version='1',budget_usd=None)
    evaluation=dict(tasks=[r('TaskBundle')],arms=[dict(arm='base',policy=policy,checkpoint=raw,training_config=None)],limits=limits,seeds=seeds,partition='locked_test',harness_version='1',checkpoint_selection_rule='frozen before test',invalid_trial_rule='report all assigned',metric='pass_at_1',episodes_per_trial=1,frozen_roster=raw,preregistration=raw)
    out={
      'CandidateRecord':dict(schema_version=2,provenance_label='reconstructed_specification',repository_url='https://example.test/repo',repository_family='family',request_lineage=['request-1'],partition='train',sources=[dict(url='https://example.test/issue/1',content=raw,retrieved_at='2026-09-19T00:00:00Z',published_at=None,edited_at=None,edit_history='unavailable',media_type='text/plain',redirect_chain=None)],license=dict(spdx_id=None,license_text=None,status='unknown',evidence=[ev]),commits=relation,screening=dict(disposition='provisional',reason='unit diagnostic',evidence=[ev])),
      'SourcePair':dict(schema_version=2,provenance_label='reconstructed_specification',candidate=r('CandidateRecord'),baseline_commit='a'*40,reference_commit='b'*40,baseline=public,reference=raw,relationship=relation,changed_files=[dict(path='src/cli.py',category='implementation',rationale='feature')],admissible_cutoff='2026-09-01T00:00:00Z',verification=[ev]),
      'RequirementContract':dict(visible_request='Add command suggestions',capability='Suggestions',entry_points=['CLI'],requirements=[req],compatibility_obligations=[],ambiguities=[],allowed_changes=allowed,public_checks=[],episode_limits=limits,provenance_label='historical_request'),
      'ScenarioPlan':dict(contract=r('RequirementContract'),mandatory_requirement_ids=['R1'],scenarios=[dict(scenario_id='S1',requirement_ids=['R1'],preconditions=['command exists'],actions=['invoke typo'],observations=['stderr'],expected_relation='suggests command',input_domain='one edit typo',oracle_origin=link,reset_needs=[])],seed_policy=seeds),
      'EnvironmentRecipe':dict(runtime_image=r('runtime-image','authoring','bytes'),image_digest='python@sha256:'+'d'*64,interpreter_version='3.13.7',dependencies=[],setup=[command],reset=[command],services=[],limits=limits,neutral_repairs=[],locale='C.UTF-8',timezone='UTC',environment=[],randomness=seeds,network_policy='none',baseline=public),
      'VerifierBundle':dict(contract=r('RequirementContract'),scenario_plan=r('ScenarioPlan'),cases=[dict(case_id='C1',requirement_ids=['R1'],inputs=raw,comparison=raw,mandatory=True)],completion_manifest=['C1'],worker_adapter=dict(code=public,version='1',supported_observables=['stderr'],limitations=[]),public_examples=[],controls=[],permissions=dict(controller_role='controller',worker_inputs=[public],output_limit_bytes=1024,submission_policy=allowed)),
      'TaskBundle':dict(state='built',partition='train',repository_family='family',request_lineage=['request-1'],source_pair=r('SourcePair'),baseline=public,solver_view=dict(instruction=public,workspace=public,public_checks=[],runtime_manifest=public,inventory=public),contract=r('RequirementContract'),environment=r('EnvironmentRecipe'),adapter_version='1',private_oracle=r('VerifierBundle'),reference_solution=raw,qualification=None),
      'QualificationReport':dict(task=r('TaskBundle'),disposition='provisional',baseline_health=None,baseline_absence=None,reference_run=None,controls=[],fresh_runs=[],interrupted_reset_runs=[],rejection_reasons=['not executed'],repair_attempts=0,policy_version='pilot-v1'),
      'RolloutRecord':dict(run_id='run-1',task=r('TaskBundle'),policy=policy,limits=limits,seeds=seeds,steps=[],submission=None,stopping_reason='infrastructure_failure',disposition='infrastructure_failure',reward=None,grading_evidence=[],training_eligible=False),
      'TrainingCheckpoint':dict(weights=raw,optimizer_state=raw,reference_checkpoint=raw,data_position=0,policy_version='p1',configuration=training,consumed_tasks=[r('TaskBundle')],optimizer_steps=0,update_evidence=[ev],reload_evidence=[ev]),
      'EvaluationReport':dict(configuration=evaluation,frozen_task_roster=[r('TaskBundle')],trials=[],paired_metrics=[],audits=[],disposition='provisional',limitations=['not executed']),
    }
    return {kind:deepcopy(common|{'kind':kind}|fields) for kind,fields in out.items()}


@pytest.mark.parametrize('kind',ARTIFACT_KINDS)
def test_all_artifact_kinds_round_trip_typed_and_immutable(tmp_path,kind):
    value=examples()[kind]
    artifact=getattr(c,kind).model_validate_json(json.dumps(value))
    store=ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    reference=store.put_artifact(artifact)
    assert store.get_artifact(reference).model_dump(mode='json') == value
    assert store.put_artifact(artifact) == reference
    with pytest.raises(ValidationError): artifact.schema_version=2
    with pytest.raises(ArtifactIntegrityError): store.get_bytes(reference)


@pytest.mark.parametrize('kind,field,bad',[
 ('SourcePair','candidate',ref()),
 ('ScenarioPlan','mandatory_requirement_ids',['R2']),
 ('VerifierBundle','completion_manifest',[]),
 ('VerifierBundle','completion_manifest',['C1','C1']),
 ('TaskBundle','private_oracle',ref('VerifierBundle','public')|{'encoding':'json'}),
 ('TaskBundle','state','released'),
 ('QualificationReport','disposition','success'),
 ('RolloutRecord','reward',1),
 ('RolloutRecord','training_eligible',True),
 ('EvaluationReport','disposition','success'),
])
def test_cross_reference_and_missing_gate_evidence_rejected(kind,field,bad):
    value=examples()[kind];value[field]=bad
    with pytest.raises(ValidationError): getattr(c,kind).model_validate_json(json.dumps(value))


def test_public_artifact_cannot_embed_private_references(tmp_path):
    value=examples()['RequirementContract']; value['visibility']='public'
    artifact=c.RequirementContract.model_validate_json(json.dumps(value))
    with pytest.raises(ArtifactIntegrityError): ArtifactStore(tmp_path,c.ActorRole.CONTROLLER).put_artifact(artifact)


def test_successful_qualification_rejects_recorded_failed_gates():
    value=examples()['QualificationReport']
    run=dict(name='gate',subject=ref(),disposition='invalid_measurement',passed=False,requirement_ids=['R1'],reason='failed',evidence=[evidence()])
    value.update(disposition='success',baseline_health=run,baseline_absence=run,reference_run=run,controls=[run],fresh_runs=[run]*3,interrupted_reset_runs=[run]*3)
    with pytest.raises(ValidationError): c.QualificationReport.model_validate_json(json.dumps(value))


def test_bool_reward_is_not_integer_reward():
    value=examples()['RolloutRecord'];value.update(disposition='success',reward=True,grading_evidence=[evidence()])
    with pytest.raises(ValidationError): c.RolloutRecord.model_validate_json(json.dumps(value))


def test_training_rejects_mismatched_policy_token_trace():
    value=examples()['RolloutRecord'];value.update(disposition='success',reward=1,grading_evidence=[evidence()],training_eligible=True,stopping_reason='submitted',submission=ref())
    value['steps']=[dict(index=0,action=ref(),observation=ref(),evidence=[evidence()],token_trace=dict(context_token_ids=[1],sampled_token_ids=[2],behavior_log_probabilities=[-0.5],assistant_loss_mask=[True],policy_version='old'))]
    with pytest.raises(ValidationError,match='episode behavior policy'): c.RolloutRecord.model_validate_json(json.dumps(value))


@pytest.mark.parametrize('kind,field',[('SourcePair','reference'),('TaskBundle','reference_solution'),('TaskBundle','private_oracle')])
@pytest.mark.parametrize('visibility',['public','authoring','training','internal'])
def test_review_reference_material_requires_privileged_visibility(kind,field,visibility):
    value=examples()[kind];value[field]['visibility']=visibility
    with pytest.raises(ValidationError):
        getattr(c,kind).model_validate_json(json.dumps(value))


def measured_rollout():
    value=examples()['RolloutRecord']
    value.update(disposition='success',reward=1,grading_evidence=[evidence()],training_eligible=True,stopping_reason='submitted',submission=ref())
    value['steps']=[dict(index=0,action=ref(),observation=ref(),evidence=[evidence()],token_trace=dict(context_token_ids=[1],sampled_token_ids=[2],behavior_log_probabilities=[-0.5],assistant_loss_mask=[True],policy_version='p1'))]
    return value


@pytest.mark.parametrize('stop',['invalid_trajectory','infrastructure_failure'])
@pytest.mark.parametrize('training_eligible',[True,False])
def test_review_invalid_stop_cannot_claim_success(stop,training_eligible):
    value=measured_rollout();value.update(stopping_reason=stop,training_eligible=training_eligible)
    with pytest.raises(ValidationError,match='stop requires'):
        c.RolloutRecord.model_validate_json(json.dumps(value))


@pytest.mark.parametrize('stop,disposition',[('invalid_trajectory','invalid_measurement'),('infrastructure_failure','infrastructure_failure')])
def test_review_untrainable_stop_preserves_saved_submission(stop,disposition):
    value=measured_rollout();value.update(stopping_reason=stop,disposition=disposition,training_eligible=False,reward=None)
    result=c.RolloutRecord.model_validate_json(json.dumps(value))
    assert result.submission is not None and result.reward is None and not result.training_eligible


def test_review_terminal_candidate_failure_remains_zero_reward():
    value=measured_rollout();value.update(stopping_reason='candidate_failure',disposition='candidate_rejection',reward=0)
    result=c.RolloutRecord.model_validate_json(json.dumps(value))
    assert result.training_eligible and result.reward==0
    value['reward']=1
    with pytest.raises(ValidationError):
        c.RolloutRecord.model_validate_json(json.dumps(value))


@pytest.mark.parametrize('stop',['malformed_action','token_limit','tool_limit','time_limit'])
@pytest.mark.parametrize('reward',[0,1])
def test_review_ordinary_stops_preserve_final_submission_grade(stop,reward):
    value=measured_rollout();value.update(stopping_reason=stop,disposition='success',reward=reward)
    result=c.RolloutRecord.model_validate_json(json.dumps(value))
    assert result.training_eligible and result.reward==reward and result.submission is not None
    value['grading_evidence']=[]
    with pytest.raises(ValidationError,match='grading evidence'):
        c.RolloutRecord.model_validate_json(json.dumps(value))


def evaluation_trial(**updates):
    value=dict(trial_id='t1',task=ref('TaskBundle')|{'encoding':'json'},repository_family='family',arm='base',policy_seed=1,case_seed=1,rollout=None,disposition='blocked_dependency',resolved=None,evidence=[evidence()])
    return value|updates


def evaluation_metric(**updates):
    return dict(name='pass_at_1',estimate=None,lower=None,upper=None,sample_size=0,method='not measured',limitations=['blocked'])|updates


@pytest.mark.parametrize('has_trial,has_metric',[(False,False),(True,False),(False,True)])
def test_review_success_requires_measured_trials_and_metrics(has_trial,has_metric):
    value=examples()['EvaluationReport'];value['disposition']='success'
    trial=evaluation_trial()
    if has_trial: trial.update(disposition='success',resolved=False,rollout=ref('RolloutRecord')|{'encoding':'json'})
    metric=evaluation_metric()
    if has_metric: metric.update(estimate=0.0,sample_size=1)
    value.update(trials=[trial],paired_metrics=[metric])
    with pytest.raises(ValidationError,match='measured'):
        c.EvaluationReport.model_validate_json(json.dumps(value))


def test_review_evaluation_preserves_invalid_trial_accounting():
    value=examples()['EvaluationReport']
    value.update(disposition='success',trials=[evaluation_trial(trial_id='valid',disposition='success',resolved=True,rollout=ref('RolloutRecord')|{'encoding':'json'}),evaluation_trial(trial_id='blocked')],paired_metrics=[evaluation_metric(estimate=1.0,sample_size=1)])
    report=c.EvaluationReport.model_validate_json(json.dumps(value))
    assert len(report.trials)==2 and report.trials[1].resolved is None
    value.update(disposition='provisional',trials=[evaluation_trial()],paired_metrics=[evaluation_metric()])
    assert c.EvaluationReport.model_validate_json(json.dumps(value)).paired_metrics[0].estimate is None


@pytest.mark.parametrize('updates',[
 {'disposition':'success','resolved':None},
 {'disposition':'success','resolved':True,'rollout':None},
 {'disposition':'blocked_dependency','resolved':False},
 {'disposition':'infrastructure_failure','resolved':True},
 {'disposition':'invalid_measurement','resolved':False},
 {'disposition':'candidate_rejection','resolved':True,'rollout':ref('RolloutRecord')|{'encoding':'json'}},
])
def test_review_trial_rejects_contradictory_outcomes(updates):
    with pytest.raises(ValidationError):c.TrialResult.model_validate_json(json.dumps(evaluation_trial(**updates)))


@pytest.mark.parametrize('kind',['CandidateRecord','SourcePair'])
@pytest.mark.parametrize('label',['historical_request','reconstructed_specification'])
def test_provenance_v2_roundtrip_binds_validated_label(tmp_path,kind,label):
    value=examples()[kind];value.update(schema_version=2,provenance_label=label)
    if kind=='SourcePair': value['candidate']['schema_version']=2
    artifact=getattr(c,kind).model_validate_json(json.dumps(value))
    store=ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    reference=store.put_artifact(artifact)
    assert reference.schema_version==2
    assert store.get_artifact(reference).provenance_label==label
    changed=value|{'provenance_label':'reconstructed_specification' if label=='historical_request' else 'historical_request'}
    assert store.put_artifact(getattr(c,kind).model_validate_json(json.dumps(changed))) != reference


@pytest.mark.parametrize('kind',['CandidateRecord','SourcePair'])
@pytest.mark.parametrize('label',[None,'unknown','',True])
def test_provenance_v2_rejects_missing_or_invalid_label(kind,label):
    value=examples()[kind];value['schema_version']=2
    value.pop('provenance_label',None)
    if label is not None: value['provenance_label']=label
    with pytest.raises(ValidationError) as failure:
        getattr(c,kind).model_validate_json(json.dumps(value))
    assert any(error['loc']==('provenance_label',) for error in failure.value.errors())


@pytest.mark.parametrize('kind',['CandidateRecord','SourcePair'])
def test_provenance_v2_rejects_unlabelled_v1_without_rewriting_store(tmp_path,kind):
    import hashlib
    from feature_rl.artifacts import canonical_json
    value=examples()[kind];value['schema_version']=1;value.pop('provenance_label',None)
    if kind=='SourcePair':value['candidate']['schema_version']=1
    serialized=canonical_json(dict(kind=kind,schema_version=1,visibility='private',encoding='json',payload=value))
    digest=hashlib.sha256(serialized).hexdigest();path=tmp_path/(digest+'.json');path.write_bytes(serialized)
    reference=c.ArtifactRef.model_validate_json(json.dumps(ref(kind)|{'sha256':digest,'encoding':'json'}))
    with pytest.raises(ArtifactIntegrityError,match='unsupported.*schema version'):
        ArtifactStore(tmp_path,c.ActorRole.CONTROLLER).get_artifact(reference)
    assert path.read_bytes()==serialized


def test_provenance_v2_construct_rejects_old_candidate_reference():
    with pytest.raises(ValidationError,match='schema version'):
        c.ConstructRequest.model_validate_json(json.dumps({'candidate':ref('CandidateRecord')|{'encoding':'json'}}))


@pytest.mark.parametrize('chain',[None,['https://example.test/issue/1'],['https://example.test/issue/1','https://archive.test/issue/1']])
def test_provenance_v2_redirect_chain_preserves_known_and_unknown(chain):
    value=examples()['CandidateRecord']['sources'][0]|{'redirect_chain':chain}
    snapshot=c.SourceSnapshot.model_validate_json(json.dumps(value))
    assert snapshot.model_dump(mode='json')['redirect_chain']==chain


@pytest.mark.parametrize('chain',['missing',[],['https://wrong.test/issue/1']])
def test_provenance_v2_redirect_chain_rejects_omission_empty_or_wrong_origin(chain):
    value=examples()['CandidateRecord']['sources'][0];value.pop('redirect_chain',None)
    if chain!='missing':value['redirect_chain']=chain
    with pytest.raises(ValidationError):c.SourceSnapshot.model_validate_json(json.dumps(value))


@pytest.mark.parametrize('seed_fields',[{}, {'case_seed':None}, {'case_seed':0}, {'case_seed':17}])
def test_run_request_case_seed_transport_roundtrip(seed_fields):
    rollout=examples()['RolloutRecord']
    value=dict(task_version=rollout['task'],policy=rollout['policy'],limits=rollout['limits'])|seed_fields
    request=c.RunRequest.model_validate_json(json.dumps(value))
    assert request.case_seed==seed_fields.get('case_seed')
    assert request.policy.seed==13
    assert request.model_dump(mode='json')==value|{'case_seed':seed_fields.get('case_seed')}
    assert c.RunRequest.model_validate_json(request.model_dump_json())==request


@pytest.mark.parametrize('seed',[-1,True,False,1.0,'1',[],{}])
def test_run_request_rejects_invalid_case_seed_in_json_and_python(seed):
    rollout=examples()['RolloutRecord']
    value=dict(task_version=rollout['task'],policy=rollout['policy'],limits=rollout['limits'])
    valid=c.RunRequest.model_validate_json(json.dumps(value))
    with pytest.raises(ValidationError):
        c.RunRequest.model_validate_json(json.dumps(value|{'case_seed':seed}))
    with pytest.raises(ValidationError):
        c.RunRequest.model_validate(valid.model_dump()|{'case_seed':seed})
