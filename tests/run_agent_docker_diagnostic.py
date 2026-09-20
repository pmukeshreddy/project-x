"""One TEST-ONLY M7 episode, actual M3/M4; no model, human or admission claim.

Only M5.verify_accepted is substituted on this isolated service/Registry. Existing
CAS objects are immutable; the original Registry is never changed. One diagnostic
symbol represents the one scripted action, not a real tokenizer probability.
"""
from m4_fixtures import runtime_policy
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess,uuid
from pathlib import Path
from feature_rl import contracts as c
from feature_rl.agents import AgentRunner,PolicyBackend,Completion,HARNESS
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import DockerEngine,EnvironmentRuntime,SandboxPolicy,PreparedEnvironment
from feature_rl.grading import GradingService,read_grade
from feature_rl.pipeline import TaskBuilder,TaskLifecycle
from feature_rl.qualification import QualificationService,QualificationPolicy
from feature_rl.registry import Registry

LABEL='TEST ONLY scripted M7 worker/limit/saved-source diagnostic; no model or human approval'
OUT=Path('docs/evidence/M7/runner/docker-receipt.json')
if OUT.exists():raise SystemExit('Existing diagnostic receipt preserved; no automatic redispatch')
old=json.loads(Path('docs/evidence/M6/fixture-runtime/receipt.json').read_text())
setup=json.loads(Path('docs/evidence/M2/coordinator-authoring-input.json').read_text())
original=Path(old['state'])
state=Path('.feature-rl/research/M7/runner-'+uuid.uuid4().hex).resolve();state.mkdir(parents=True)
original_registry_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (original/'registry').iterdir() if p.is_file()}
shutil.copytree(original/'registry',state/'registry')
store=ArtifactStore(original/'store',c.ActorRole.CONTROLLER)
registry=Registry(state/'registry',store)
revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
result={'label':LABEL,'state':str(state),'original_cas':str(store.root),'scope':'unit_diagnostic',
    'human_approval':False,'model_calls':0,'training_eligible':False,'started_at':datetime.now(timezone.utc).isoformat(),
    'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('src/feature_rl/agents').glob('*.py')}}
def save():OUT.write_text(json.dumps(result,indent=2)+'\n')
def ref(v):return c.ArtifactRef.model_validate_json(json.dumps(v))
save()
try:
    print('Qualifying actual M3 boundary using cached image',flush=True)
    engine=DockerEngine(state_root=state/'runtime',socket_path=Path(setup['socket_path']),policy=runtime_policy())
    engine.qualify_boundary()
    runtime=EnvironmentRuntime(store=store,engine=engine,revision=old['context']['runtime_revision'])
    grader=GradingService(store=store,runtime=runtime,revision=old['context']['grading_revision'])
    builder=TaskBuilder(store=store,registry=registry,revision=old['context']['builder_revision'])
    qualification=QualificationService(store=store,registry=registry,grader=grader,builder=builder,
        revision=revision,policy=QualificationPolicy(policy_id='m7-scripted-diagnostic-only'))
    lifecycle=TaskLifecycle(store=store,registry=registry,qualification=qualification,revision=revision)
    task_ref=ref(old['context']['task'])
    q_ref=next(ref(r) for r in old['qualification']['result']['artifacts'] if r['kind']=='QualificationReport')
    q=store.get_artifact(q_ref)
    assert q.disposition==c.Disposition.PROVISIONAL
    try:qualification.verify_accepted(task_ref,q_ref)
    except Exception as exc:result['unmodified_M5_denial']=type(exc).__name__+': '+str(exc)
    else:raise AssertionError('actual M5 unexpectedly admitted provisional evidence')
    task=store.get_artifact(task_ref);limits=store.get_artifact(task.contract).episode_limits
    class OneShot(PolicyBackend):
        max_seq_len=2;vocab_size=3;tokenizer_digest='a'*64;template_digest='b'*64
        calls=0
        def verify_policy(self,policy):
            assert policy.identity.provider=='unit-diagnostic' and not policy.require_token_probabilities
        def render(self,messages):return json.dumps(messages),(1,)
        def generate(self,context,*,policy,max_tokens,timeout,session_id):
            self.calls+=1
            # Source and test are the already disclosed synthetic prefix fixture,
            # never the historical H implementation. Python runs only inside M3.
            code="""from pathlib import Path
p=Path('src/click/__init__.py')
print('read baseline bytes',len(p.read_bytes()))
Path('src/click/m5_test_feature.py').write_text('def transform(text):\\n    return "fixture:" + text\\n')
from click.m5_test_feature import transform
assert transform('alpha')=='fixture:alpha'
print('disclosed public test passed')
"""
            text=json.dumps({'action':'public_test','argv':['python','-c',code],'stdin':'','timeout_seconds':30.0})
            return Completion(text,context,(2,),None,policy.policy_version,'stop')
    backend=OneShot()
    system=store.put_bytes(LABEL.encode(),'system-prompt',c.Visibility.PUBLIC)
    policy=c.PolicyConfig(identity=c.ModelIdentity(provider='unit-diagnostic',model='scripted-one-shot',revision='1',
        weights=None,tokenizer_digest=None),policy_version='m7-scripted-diagnostic',temperature=0.,top_p=1.,seed=101,
        system_prompt=system,harness_version=HARNESS,require_token_probabilities=False)
    runner=AgentRunner(store=store,registry=registry,lifecycle=lifecycle,builder=builder,runtime=runtime,
        grader=grader,backend=backend,revision=revision,evidence_scope='unit_diagnostic')
    print('Checking actual unmodified runner admission denial',flush=True)
    denied=runner.run(task_ref,policy,limits,case_seed=19,invocation='m7-scripted-denial')
    denied_record=store.get_artifact(next(r for r in denied.artifacts if r.kind=='RolloutRecord'))
    assert denied_record.reward is None and backend.calls==0
    result['admission_denial']=denied.model_dump(mode='json');save()
    # The sole production-method substitution: explicit test-local M5 gate.
    def diagnostic_only(task,report):
        assert report==q_ref
        return q
    qualification.verify_accepted=diagnostic_only
    qualified=lifecycle.qualify(task_ref,q_ref)
    released=lifecycle.release(qualified.artifacts[0])
    assert released.disposition==c.Disposition.SUCCESS
    release_ref=released.artifacts[0]
    print('Executing one scripted episode: actual read/edit/public-test; token limit; actual M4 grade',flush=True)
    run=runner.run(release_ref,policy,limits,case_seed=19,invocation='m7-scripted-one-shot')
    rollout=store.get_artifact(next(r for r in run.artifacts if r.kind=='RolloutRecord'))
    result['run']=run.model_dump(mode='json');result['rollout']=rollout.model_dump(mode='json');save()
    assert backend.calls==1 and len(rollout.steps)==1 and rollout.stopping_reason==c.StopReason.TOKEN_LIMIT
    assert rollout.reward==1 and not rollout.training_eligible
    grade_refs=[r for e in rollout.grading_evidence if e.producer=='feature_rl.grading' for r in e.artifacts if r.kind=='m4-grade-receipt']
    assert len(grade_refs)==1
    grade=read_grade(store,grade_refs[0])
    assert grade.case_seed==19 and grade.task==release_ref and grade.submission==rollout.submission
    assert len(grade.cases)==3 and all(x.passed for x in grade.cases)
    assert runner.validate_record(rollout).reward==1
    assert runner.run(release_ref,policy,limits,case_seed=19,invocation='m7-scripted-one-shot')==run and backend.calls==1
    # Fresh episode baseline and M3's actual terminal reset; no second feature grade.
    prepared=PreparedEnvironment.model_validate_json(json.dumps(old['context']['environment']))
    fresh=runtime.open_workspace(prepared,role='candidate',allowed_changes=store.get_artifact(task.contract).allowed_changes)
    before=runtime.workspace(fresh)[3]
    reset=runtime.reset(fresh);closed=runtime.close(fresh)
    assert before.raw_sha256==reset.raw_sha256==closed.raw_sha256
    assert 'src/click/m5_test_feature.py' not in runtime.source(reset.artifact).files
    result['fresh_reset']=reset.model_dump(mode='json')
    result['status']='passed';result['backend_calls']=backend.calls;result['grade_case_count']=len(grade.cases)
except Exception as exc:
    result['status']='failed';result['failure']=type(exc).__name__+': '+str(exc);save();raise
finally:
    result['original_registry_unchanged']=original_registry_hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (original/'registry').iterdir() if p.is_file()}
    result['finished_at']=datetime.now(timezone.utc).isoformat();save()
print('PASS: one diagnostic episode, one measured terminal grade, three actual cases; original Registry unchanged',flush=True)
