"""CLI diagnostics use real private M0/Registry state; no worker or model runs."""
import importlib
import json
from pathlib import Path

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from test_factory_source import setup


def api():
    return importlib.import_module('feature_rl.cli')


def write(path,value):
    path.write_bytes(canonical_json(value))
    return str(path)


def context(tmp_path):
    _,factory,candidate=setup(tmp_path)
    config=write(tmp_path/'cli.json',{'version':'m6-cli-v1',
        'store_root':str(factory.store.root),'registry_root':str(factory.registry.root),
        'revision':factory.revision})
    request=write(tmp_path/'request.json',{'candidate':candidate.model_dump(mode='json')})
    return factory,config,request


def test_help_and_configuration_schema_do_not_require_runtime(capsys):
    with pytest.raises(SystemExit) as status:api().main(['--help'])
    assert status.value.code==0
    assert 'construct' in capsys.readouterr().out
    assert api().main(['config-schema'])==0
    schema=json.loads(capsys.readouterr().out)
    assert schema['additionalProperties'] is False
    assert 'runtime' in schema['properties']


def test_screen_source_uses_actual_selected_registry_job_and_replays(tmp_path,capsys):
    factory,config,request=context(tmp_path)
    args=['--config',config,'screen-source','--request',request]
    assert api().main(args)==0
    first=json.loads(capsys.readouterr().out)
    assert first['disposition']=='success' and first['operation']=='construct'
    before=factory.registry.events(limit=1000)
    assert api().main(args)==0
    assert json.loads(capsys.readouterr().out)==first
    assert factory.registry.events(limit=1000)==before


def test_missing_construction_inputs_returns_selected_typed_failure(tmp_path,capsys):
    factory,config,request=context(tmp_path)
    assert api().main(['--config',config,'construct','--request',request])!=0
    value=json.loads(capsys.readouterr().out)
    assert value['disposition']=='blocked_dependency'
    assert value['artifacts'] and all(ref['kind']!='TaskBundle' for ref in value['artifacts'])
    assert value['costs']


@pytest.mark.parametrize('bad',[
    '{"revision":"a","revision":"b"}',
    '{"version":"m6-cli-v1","execute_python":"print(1)"}',
])
def test_invalid_json_configuration_is_machine_readable_failure(tmp_path,capsys,bad):
    config=tmp_path/'bad.json';config.write_text(bad)
    request=write(tmp_path/'request.json',{})
    assert api().main(['--config',str(config),'construct','--request',request])!=0
    value=json.loads(capsys.readouterr().out)
    assert value['operation']=='construct' and value['disposition']!='success'
    assert not value['artifacts'] and value['costs']


def test_runtime_prerequisite_checked_before_composition(tmp_path,capsys):
    _,config,_=context(tmp_path)
    request=write(tmp_path/'task.json',{'task_version':{'sha256':'0'*64,'kind':'TaskBundle',
        'schema_version':1,'visibility':'private','encoding':'json'}})
    assert api().main(['--config',config,'qualify','--request',request])!=0
    value=json.loads(capsys.readouterr().out)
    assert value['disposition']=='blocked_dependency'
    assert 'runtime' in value['reason'].lower()


def test_cli_does_not_accept_unknown_request_fields(tmp_path,capsys):
    factory,config,request=context(tmp_path)
    value=json.loads(Path(request).read_text());value['approved']=True
    Path(request).write_text(json.dumps(value))
    before=factory.registry.events(limit=1000)
    assert api().main(['--config',config,'construct','--request',request])!=0
    assert json.loads(capsys.readouterr().out)['disposition']!='success'
    assert factory.registry.events(limit=1000)==before


def test_symlink_configuration_rejects_without_following(tmp_path,capsys):
    _,config,request=context(tmp_path)
    link=tmp_path/'linked.json';link.symlink_to(config)
    assert api().main(['--config',str(link),'construct','--request',request])!=0
    assert json.loads(capsys.readouterr().out)['disposition']!='success'


def test_lost_publication_reply_retains_exact_private_recovery_capability(tmp_path,capsys,monkeypatch):
    import base64
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.pipeline import FactoryPublicationFailed
    from feature_rl.registry import Claim
    factory,config,request=context(tmp_path)
    original=ArtifactStore.put_bytes
    def lost(store,data,kind,visibility):
        result=original(store,data,kind,visibility)
        if kind=='m6-source-disposition':raise OSError('TEST ONLY lost publication reply')
        return result
    monkeypatch.setattr(ArtifactStore,'put_bytes',lost)
    assert api().main(['--config',config,'screen-source','--request',request])!=0
    captured=capsys.readouterr()
    recovery=json.loads(captured.err)['recovery']['state']
    claim=Claim.model_validate_json(canonical_json(recovery['claim']['value']))
    payload=base64.b64decode(recovery['payload']['bytes_base64'])
    pending=FactoryPublicationFailed('retained TEST receipt',claim,payload,kind=recovery['kind'])
    assert pending.sha256==recovery['sha256']
    monkeypatch.setattr(ArtifactStore,'put_bytes',original)
    result=factory.retry_publication(pending)
    assert result.disposition==c.Disposition.SUCCESS
    assert factory.store.get_bytes(result.artifacts[0])==payload


def test_actual_m5_frozen_capability_retains_datetimes_models_and_bytes():
    from datetime import datetime,timezone
    from feature_rl.qualification.models import QualificationPublicationFailed,FrozenPublication
    now=datetime.now(timezone.utc)
    # Exact concrete M5 capability type and frozen value shapes, no signature or gate.
    error=QualificationPublicationFailed('TEST retained bytes',pending=FrozenPublication(None,
        {'recorded_at':now,'verification':b'TEST ONLY'},'human_verification'))
    value=api()._retained(error)['state']['pending']['state']['payload']
    assert value['recorded_at']['datetime_iso8601']==now.isoformat()
    assert value['verification']['bytes_base64']=='VEVTVCBPTkxZ'


def test_complete_command_surface_has_real_request_and_recovery_options(capsys):
    for name in ('author','import-authoring','grade','run','train','evaluate','audit','recover','retry-publication'):
        with pytest.raises(SystemExit) as status:api().main([name,'--help'])
        assert status.value.code==0
        assert '--' in capsys.readouterr().out


def test_grade_command_uses_actual_factory_and_retained_original_retry(tmp_path,capsys,monkeypatch):
    from test_factory_grade import setup as grade_setup
    from feature_rl.pipeline.configuration import Application
    factory,task,submission=grade_setup(tmp_path)
    config=write(tmp_path/'cli.json',{'store_root':str(factory.store.root),'registry_root':str(factory.registry.root),'revision':factory.revision})
    request=write(tmp_path/'grade.json',c.GradeRequest(task_version=task,submission=submission,case_seed=73).model_dump(mode='json'))
    modes=[]
    def compose(config,**kwargs):
        modes.append(kwargs);return Application(factory,factory.grading.runtime,factory.grading,None,None)
    monkeypatch.setattr(api(),'compose',compose)
    put=factory.store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m6-grade-original':raise OSError('TEST CLI original result outage')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    assert api().main(['--config',config,'grade','--request',request,'--invocation','TEST_CLI_GRADE'])==2
    captured=capsys.readouterr();retained=tmp_path/'recovery.json';retained.write_text(captured.err)
    assert json.loads(captured.out)['operation']=='grade' and modes[-1]['runtime']
    monkeypatch.setattr(factory.store,'put_bytes',put)
    def forbidden(*args,**kwargs):raise AssertionError('known grade repeated')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    assert api().main(['--config',config,'retry-publication','--service','grade','--receipt',str(retained)])==1
    value=json.loads(capsys.readouterr().out)
    from feature_rl.grading import read_grade
    assert read_grade(factory.store,c.ArtifactRef.model_validate_json(canonical_json(value['artifacts'][0]))).case_seed==73


@pytest.mark.parametrize('service_name',('native_run','training','evaluation'))
def test_terminal_service_cleanup_preserves_original_and_cleanup_failures(tmp_path,monkeypatch,service_name):
    from feature_rl.pipeline.configuration import Application
    from feature_rl.agents.native_service import NativeRunService
    from feature_rl.training.service import TrainingService
    from feature_rl.evaluation.service import EvaluationService
    _,factory,_=setup(tmp_path)
    cls={'native_run':NativeRunService,'training':TrainingService,'evaluation':EvaluationService}[service_name]
    # Delivery-only diagnostic: actual service type, no constructor/startup work.
    service=object.__new__(cls);service.store=factory.store;service.registry=factory.registry
    setattr(factory,service_name,service);closed=[]
    def close(self):closed.append(True);raise OSError('TEST cleanup publication outage')
    monkeypatch.setattr(cls,'close',close)
    app=Application(factory,None,None,None,None)
    def failure():raise RuntimeError('TEST original failure')
    with pytest.raises(RuntimeError,match='original failure') as error:api()._with_cleanup(app,failure)
    retained=api()._retained(error.value)
    assert closed==[True] and 'cli_cleanup_error' in retained['state']
    assert 'cleanup publication outage' in json.dumps(retained)


def test_missing_native_and_audit_settings_fail_before_runtime_or_state(tmp_path,monkeypatch):
    from feature_rl.pipeline.configuration import CLIConfiguration,compose,ConfigurationRequired
    config=CLIConfiguration(store_root=str(tmp_path/'absent-store'),registry_root=str(tmp_path/'absent-registry'),revision='a'*40)
    for mode in ('run','train','evaluate'):
        with pytest.raises(ConfigurationRequired,match='native'):compose(config,native_operation=mode)
    with pytest.raises(ConfigurationRequired,match='audit'):compose(config,audit=True)
    assert not (tmp_path/'absent-store').exists()


def test_thin_run_train_evaluate_and_audit_forward_exact_requests(tmp_path,capsys,monkeypatch):
    from test_training_factory import factory_fixture
    from feature_rl.pipeline import Factory
    from feature_rl.pipeline.configuration import Application
    from feature_rl.agents.native_service import NativeRunService
    from feature_rl.training.service import TrainingService
    from feature_rl.evaluation.service import EvaluationService
    from feature_rl.audits.service import AuditService
    from feature_rl.qualification.evidence import unknown_cost
    f=factory_fixture(tmp_path,monkeypatch);base=f.fixture
    factory=Factory(store=base.store,registry=base.registry,revision='f'*40,builder=base.runner.builder)
    config=write(tmp_path/'cli.json',{'store_root':str(base.store.root),'registry_root':str(base.registry.root),'revision':factory.revision})
    calls=[];closed=[];modes=[]
    for attr,cls,method in (('native_run',NativeRunService,'run'),('training',TrainingService,'train'),
            ('evaluation',EvaluationService,'evaluate'),('audit_service',AuditService,'audit')):
        # Delivery-only substituted entrypoints; no episode/update/study/human claim.
        service=object.__new__(cls);service.store=base.store;service.registry=base.registry
        setattr(factory,attr,service)
        def delivered(self,*args,_method=method,**kwargs):
            calls.append((_method,args,kwargs))
            return c.OperationResult(operation=_method,disposition=c.Disposition.BLOCKED,artifacts=(),evidence=(),
                costs=(unknown_cost('construction','TEST CLI delivery only; service execution intentionally absent'),),reason='TEST delivery only')
        monkeypatch.setattr(cls,method,delivered)
        if method!='audit':monkeypatch.setattr(cls,'close',lambda self,_method=method:closed.append(_method))
    def compose(config,**kwargs):
        modes.append(kwargs);return Application(factory,None,None,None,None)
    monkeypatch.setattr(api(),'compose',compose)
    request=c.RunRequest(task_version=base.task,policy=f.config.initial_policy,limits=base.limits,case_seed=73)
    run=write(tmp_path/'run.json',request.model_dump(mode='json'))
    assert api().main(['--config',config,'run','--request',run,'--invocation','RUN_ONE'])==1
    assert calls[-1]==('run',(base.task,f.config.initial_policy,base.limits),{'case_seed':73,'invocation':'RUN_ONE'})
    assert modes[-1]['native_operation']=='run'
    value=json.loads(Path(run).read_text());value.pop('case_seed');Path(run).write_text(json.dumps(value))
    assert api().main(['--config',config,'run','--request',run,'--invocation','RUN_DEFAULT'])==1
    assert calls[-1][2]['case_seed'] is None
    train=write(tmp_path/'train.json',{'config':f.config.model_dump(mode='json')})
    resume=write(tmp_path/'resume.json',f.config.reference_checkpoint.model_dump(mode='json'))
    assert api().main(['--config',config,'train','--request',train,'--invocation','TRAIN_RECOVER','--resume',resume])==1
    assert calls[-1]==('train',(f.config,),{'invocation':'TRAIN_RECOVER','resume':f.config.reference_checkpoint})
    evaluation=c.EvaluationConfig(tasks=(base.task,),arms=(c.EvaluationArm(arm='base',policy=f.config.initial_policy,
        checkpoint=f.config.reference_checkpoint,training_config=None),),limits=base.limits,seeds=f.config.seeds,
        partition=c.Partition.LOCKED_TEST,harness_version=f.config.initial_policy.harness_version,
        checkpoint_selection_rule='TEST frozen rule',invalid_trial_rule='TEST retain invalid',metric='pass_at_1',episodes_per_trial=1,
        frozen_roster=base.task,preregistration=base.task)
    evaluate=write(tmp_path/'evaluate.json',{'config':evaluation.model_dump(mode='json')})
    assert api().main(['--config',config,'evaluate','--request',evaluate])==1
    assert calls[-1]==('evaluate',(evaluation,),{}) and modes[-1]['native_operation']=='evaluate'
    audit=write(tmp_path/'audit.json',{'run_ids':['TEST_SELECTED_RUN']})
    assert api().main(['--config',config,'audit','--request',audit])==1
    assert calls[-1]==('audit',(('TEST_SELECTED_RUN',),),{}) and modes[-1]['audit']
    assert api().main(['--config',config,'audit','--source-only'])==1
    assert calls[-1]==('audit',((),),{})
    assert closed==['run','train','evaluate']*6
    assert all(json.loads(line)['disposition']=='blocked_dependency' for line in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize('mode',('run','train','evaluate'))
def test_actual_native_composition_is_inert_and_shares_real_services(tmp_path,monkeypatch,mode):
    from test_training_factory import factory_fixture
    from feature_rl.pipeline.configuration import CLIConfiguration,compose
    from feature_rl.environments import DockerEngine
    from feature_rl.agents.native_service import NativeRunService
    from feature_rl.training.service import TrainingService
    from feature_rl.evaluation.service import EvaluationService
    from feature_rl.training.factory import NativeSessionFactory
    f=factory_fixture(tmp_path,monkeypatch);base=f.fixture;boundaries=[]
    # TEST boundary qualification only; actual M3/M4/M5/M6/M7/M8 constructors
    # compose. No Docker command or native constructor may run in this check.
    def engine(self,**kwargs):self.policy=kwargs['policy'];self.qualified=False
    def qualified(self):
        boundaries.append('TEST boundary');self.qualified=True
        self.qualification={'observations':{'scope':'unit_diagnostic'}}
    monkeypatch.setattr(DockerEngine,'__init__',engine)
    monkeypatch.setattr(DockerEngine,'qualify_boundary',qualified)
    def forbidden(*args,**kwargs):raise AssertionError('composition dispatched native startup')
    monkeypatch.setattr(NativeSessionFactory,'create',forbidden)
    config=CLIConfiguration.model_validate_json(canonical_json({'store_root':str(base.store.root),
        'registry_root':str(base.registry.root),'revision':'f'*40,'builder_revision':base.runner.builder.revision,
        'runtime':{'state_root':str(tmp_path/'runtime'),'socket_path':str(tmp_path/'docker.sock'),
            'revision':base.runner.runtime.revision,'grading_revision':base.runner.grader.revision,
            'policy':base.runner.runtime.policy.model_dump(mode='json')},
        'qualification':{'revision':'e'*40},'native':{'revision':'f'*40,
            'settings':f.service.settings.model_dump(mode='json'),'bootstrap':f.config.model_dump(mode='json')},
        'evaluation':{'revision':'8'*40}}))
    app=compose(config,native_operation=mode)
    service={'run':app.factory.native_run,'train':app.factory.training,'evaluate':app.factory.evaluation}[mode]
    assert type(service) is {'run':NativeRunService,'train':TrainingService,'evaluate':EvaluationService}[mode]
    assert service.store is app.factory.store and service.registry is app.factory.registry
    assert service.lifecycle is app.resolver and service.builder is app.factory.builder
    assert service.runtime is app.runtime and service.grader is app.grader
    if mode=='evaluate':assert service.factory is app.factory
    assert boundaries==['TEST boundary']
    app.close()


def test_authoring_and_inert_import_deliver_actual_closed_call_without_generation(tmp_path,capsys,monkeypatch):
    from test_factory_authoring import setup as authoring_setup
    from feature_rl.pipeline.configuration import Application
    from feature_rl.qualification.evidence import unknown_cost
    factory,candidate,call,runner=authoring_setup(tmp_path,monkeypatch)
    config=write(tmp_path/'cli.json',{'store_root':str(factory.store.root),'registry_root':str(factory.registry.root),
        'revision':factory.revision,'authoring':factory.authoring.model_dump(mode='json')})
    request=write(tmp_path/'candidate.json',{'candidate':candidate.model_dump(mode='json')})
    call_file=write(tmp_path/'call.json',call.model_dump(mode='json'))
    journal=factory.store.put_bytes(b'TEST delivery-only journal','GenerationJournal',c.Visibility.PRIVATE)
    journals=write(tmp_path/'journals.json',[journal.model_dump(mode='json')]);calls=[]
    monkeypatch.setattr(api(),'compose',lambda config,**kwargs:Application(factory,None,None,None,None))
    def author(selected,*,call):
        calls.append(('author',selected,call));return c.OperationResult(operation='construct',disposition=c.Disposition.BLOCKED,
            artifacts=(),evidence=(),costs=(unknown_cost('authoring','TEST delivery only; no provider call'),),reason='TEST delivery only')
    def imported(selected,*,call,journal_refs):
        calls.append(('import',journal_refs));return author(selected,call=call)
    monkeypatch.setattr(factory,'author',author);monkeypatch.setattr(factory,'import_rejected_authoring',imported)
    assert api().main(['--config',config,'author','--request',request,'--call',call_file])==1
    assert calls[-1]==('author',candidate,call)
    assert api().main(['--config',config,'import-authoring','--request',request,'--call',call_file,'--journals',journals])==1
    assert calls[-2]==('import',(journal,)) and calls[-1]==('author',candidate,call)
    assert not runner.calls
    assert all(json.loads(line)['operation']=='construct' for line in capsys.readouterr().out.splitlines())
