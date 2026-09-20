"""Real diagnostic Click-B source→rebuild→worker→controller checks.

No H, historical private records, model calls or feature-qualification claims.
All changed Python is inert host data and executes only through reviewed M3.
"""
import os
from m4_fixtures import runtime_policy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import DockerEngine, EnvironmentRuntime, SandboxPolicy, SourceArchive, SourceFile
from feature_rl.grading import GradingService, read_grade
from feature_rl.submission import SubmissionService
from m4_fixtures import diagnostic


@pytest.fixture(scope='module')
def route():
    setup=json.loads(Path('docs/evidence/M2/coordinator-authoring-input.json').read_text())
    # Only this B ref is read from the author-role store, never its inventory.
    source_store=ArtifactStore(Path(setup['source_store']),c.ActorRole.AUTHOR)
    baseline=c.ArtifactRef.model_validate_json(json.dumps(setup['authoring_view']['baseline']))
    data=source_store.get_bytes(baseline,max_envelope_bytes=24*1024*1024,max_payload_bytes=16*1024*1024)
    state=Path('.feature-rl/research/M4')/('core-'+uuid.uuid4().hex)
    store=ArtifactStore(state/'store',c.ActorRole.CONTROLLER)
    assert store.put_bytes(data,'source-archive',c.Visibility.AUTHORING)==baseline
    pins=[]
    for wheel in setup['dependency_wheels']:
        data=Path(wheel['local_path']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==wheel['sha256']
        ref=store.put_bytes(data,'dependency-wheel',c.Visibility.AUTHORING)
        pins.append(c.DependencyPin(name=wheel['name'],version=wheel['version'],artifact=ref,sha256=wheel['sha256']))
    engine=DockerEngine(image_repository=os.environ.get('FEATURE_RL_TEST_IMAGE_REPOSITORY'),state_root=state/'runtime',socket_path=Path(setup['socket_path']),policy=runtime_policy())
    engine.qualify_boundary()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    runtime=EnvironmentRuntime(store=store,engine=engine,revision=revision)
    evidence=c.EvidenceRecord(producer='M4 diagnostic B-only input verification',command=('pytest','tests/test_grading_docker.py'),
        recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision=revision,scope='source_inspection')
    prepared=runtime.create_recipe(baseline,tuple(pins),source_evidence=evidence)
    task=diagnostic(store,baseline=baseline,environment=prepared.recipe)
    rules=store.get_artifact(store.get_artifact(task).contract).allowed_changes
    submissions=SubmissionService(store=store,policy=runtime.policy)
    base=submissions.source(baseline)
    service=GradingService(store=store,runtime=runtime,revision=revision)
    print('\nM4_CONTEXT '+json.dumps({'diagnostic_only':True,'state':str(state),'baseline':baseline.model_dump(mode='json'),
        'task':task.model_dump(mode='json'),'environment':prepared.model_dump(mode='json'),'revision':revision,
        'm4_sources':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for folder in ('verifiers','submission','grading')
            for path in sorted((Path('src/feature_rl')/folder).glob('*.py'))}}),flush=True)
    yield store,runtime,service,submissions,task,baseline,rules,base
    assert runtime.recover_owned()==[]


def submission_for(route,append=b'',extra=None):
    store,runtime,service,submissions,task,baseline,rules,base=route
    files={} if not append else {'src/click/__init__.py':SourceFile(base.files['src/click/__init__.py'].data+b'\n'+append,False)}
    files.update(extra or {})
    return submissions.create(baseline,SourceArchive(files).to_tar(),(),rules)


def record(route,label,submission,seed=11):
    store,runtime,service,*_=route
    result=service.grade(route[4],submission,seed)
    receipt=read_grade(store,result.artifacts[0])
    print('\nM4_ROUTE '+json.dumps({'label':label,'result':result.model_dump(mode='json'),'receipt':receipt.model_dump(mode='json')}),flush=True)
    return receipt


@pytest.mark.parametrize('label,append,expected',[
    ('baseline',b'',1),
    ('regression',b'def echo(*args, **kwargs):\n    print("wrong")\n',0),
    ('hardcoded',b'def echo(*args, **kwargs):\n    print("public-example")\n',0),
    ('early_exit',b'import os\nos._exit(0)\n',0),
    ('crash',b'raise RuntimeError("diagnostic candidate crash")\n',0),
    ('nonterminating',b'while True: pass\n',0),
    ('malformed_json',b'import os\nos.write(1,b"not-json");os._exit(0)\n',0),
    ('duplicate_probe',b'import os\nos.write(1,b\'{"case_id":"c0","observations":{"output":"x","exit":0}}\\n\'*2);os._exit(0)\n',0),
    ('forged_reward',b'import pathlib,os\npathlib.Path("/workspace/reward.txt").write_text("1")\nos.write(1,b\'{"case_id":"c0","observations":{},"passed":true}\');os._exit(0)\n',0),
    ('excessive_output',b'import os\nwhile True: os.write(1,b"x"*65536)\n',0),
],ids=['baseline','regression','hardcoded','early_exit','crash','nonterminating','malformed_json','duplicate_probe','forged_reward','excessive_output'])
def test_actual_source_rebuild_observation_grade(route,label,append,expected):
    receipt=record(route,label,submission_for(route,append))
    assert receipt.reward==expected and receipt.cleanup_verified
    assert [case.case_id for case in receipt.cases]==['c0','c1']
    if expected==1:assert all(c.status=='completed' and c.passed for c in receipt.cases)
    assert route[1].recover_owned()==[]


def test_dependency_shadow_and_retained_state_do_not_replace_controller(route):
    # src/json.py is never an installed Click wheel member or controller module.
    submission=submission_for(route,extra={'src/json.py':SourceFile(b'raise RuntimeError("shadow")',False)})
    assert record(route,'dependency_shadow',submission).reward==1
    # Earlier forgery wrote worker-only state. A fresh baseline grade still runs
    # every expected case; no solver/runtime/verdict state transfers to it.
    assert record(route,'after_hostile_state',submission_for(route)).reward==1


def test_actual_outage_is_null_and_retry_preserves_source_and_cases(route,monkeypatch):
    store,runtime,service,*_=route
    submission=submission_for(route)
    # Actual failed connection to an absent endpoint, not a synthesized result.
    with monkeypatch.context() as change:
        change.setattr(runtime.engine,'socket_path',str(runtime.engine.state.path/'absent.sock'))
        outage=record(route,'actual_missing_socket',submission,19)
    assert outage.reward is None and outage.disposition==c.Disposition.INFRASTRUCTURE
    runtime.recover_owned()
    recovered=record(route,'same_source_seed_retry',submission,19)
    assert recovered.reward==1
    assert recovered.submission==outage.submission and recovered.manifest==outage.manifest


def test_actual_failed_build_keeps_cost_and_zero_receipt(route):
    store,runtime,service,*_=route
    # Reviewed wheel's flit_core.common.Module rejects both src/click.py and
    # src/click/ existing. This is a build error, not a later import error.
    submission=submission_for(route,extra={'src/click.py':SourceFile(b'# conflicting module source',False)})
    result=service.grade(route[4],submission,11)
    receipt=read_grade(store,result.artifacts[0])
    print('\nM4_BUILD_FAILURE '+json.dumps({'result':result.model_dump(mode='json'),'receipt':receipt.model_dump(mode='json')}),flush=True)
    assert receipt.reward==0 and receipt.build_evidence is not None and receipt.cleanup_verified
    assert all(case.status=='not_run' for case in receipt.cases)
    assert any(cost.category=='construction' and cost.wall_seconds>0 for cost in result.costs)


def test_actual_submission_path_rejects_archive_abuse_before_build(route):
    from test_submission import tar
    import tarfile
    store,runtime,service,*_=route
    for label,archive in [('traversal',tar('../escape')),('absolute',tar('/outside')),
        ('symlink',tar('src/x.py',kind=tarfile.SYMTYPE,link='/outside')),
        ('hardlink',tar('src/x.py',kind=tarfile.LNKTYPE,link='src/click/__init__.py')),
        ('device',tar('src/x.py',kind=tarfile.CHRTYPE)),('reward_path',tar('reward.txt')),
        ('dependency_change',tar('pyproject.toml')),('compressed',b'\x1f\x8bnot-a-tar')]:
        delta=store.put_bytes(archive,'m4-source-delta',c.Visibility.PRIVATE)
        payload={'version':'m4-submission-v1','baseline':route[5].model_dump(mode='json'),
            'changes':delta.model_dump(mode='json'),'deletions':[]}
        submission=store.put_bytes(canonical_json(payload),'m4-submission',c.Visibility.PRIVATE)
        receipt=record(route,'archive_'+label,submission)
        assert receipt.reward==0 and receipt.source is None and receipt.build_evidence is None
