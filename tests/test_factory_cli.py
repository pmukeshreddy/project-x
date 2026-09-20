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
