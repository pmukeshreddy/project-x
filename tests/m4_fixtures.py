"""Explicitly hand-authored DIAGNOSTIC artifacts, never a feature contract.

The ordinary echo preservation probe exercises Click's public CLI. It says
nothing about the requested missing feature, H, model generation or admission.
"""
from datetime import datetime, timezone
import json
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import SourceArchive, SourceFile


ADAPTER='''import json,sys
import click
from click.testing import CliRunner
request=json.load(sys.stdin)
@click.command()
@click.argument("word")
def command(word):
    click.echo(word)
result=CliRunner().invoke(command,[request["inputs"]["word"]])
print(json.dumps({"case_id":request["case_id"],"observations":{"output":result.output,"exit":result.exit_code}}))
'''


def diagnostic(store, *, baseline=None, environment=None, adapter=ADAPTER, case_count=2):
    now=datetime(2026,9,19,tzinfo=timezone.utc)
    def raw(data,kind='m4-diagnostic',visibility=c.Visibility.PRIVATE):
        return store.put_bytes(data if isinstance(data,bytes) else canonical_json(data),kind,visibility)
    source=raw(b'DIAGNOSTIC ONLY: CLI echoes supplied word with a newline.',visibility=c.Visibility.PUBLIC)
    evidence=c.EvidenceRecord(producer='M4 diagnostic fixture, not qualification',command=('diagnostic-fixture',),
        recorded_at=now,exit_status=0,artifacts=(source,),revision='a'*40,scope='unit_diagnostic')
    cost=c.CostRecord(category='verifier',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note='Hand-authored diagnostic fixture; no empirical qualification')
    common=dict(schema_version=1,visibility=c.Visibility.PRIVATE,provenance=c.Provenance(producer='diagnostic',
        producer_version='1',created_at=now,inputs=(source,),evidence=(evidence,)),costs=(cost,))
    origin=c.EvidenceLink(source=source,locator='entire diagnostic text',quote='CLI echoes supplied word with a newline.',provenance_label='reconstructed_specification')
    rules=c.AllowedChanges(source_roots=('src',),forbidden_paths=(),dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
    limits=c.ResourceLimits(wall_seconds=120.0,cpu_seconds=60.0,memory_bytes=512*1024*1024,pids=64,
        output_bytes=2*1024*1024,disk_bytes=128*1024*1024,tool_calls=100,input_tokens=1,output_tokens=1)
    req=c.Requirement(requirement_id='echo',statement='DIAGNOSTIC ONLY echo supplied word',mandatory=True,evidence=(origin,),observable='CLI output')
    contract=c.RequirementContract(kind='RequirementContract',**common,visible_request='DIAGNOSTIC ONLY',capability='ordinary echo diagnostic',
        entry_points=('click.command','click.echo','click.testing.CliRunner.invoke'),requirements=(req,),compatibility_obligations=(),
        ambiguities=(),allowed_changes=rules,public_checks=(),episode_limits=limits,provenance_label='reconstructed_specification')
    contract_ref=store.put_artifact(contract)
    scenarios=tuple(c.Scenario(scenario_id='s'+str(i),requirement_ids=('echo',),preconditions=('echo command registered',),
        actions=('invoke with supplied word',),observations=('output and API exit',),expected_relation='word followed by newline; exit zero',
        input_domain='diagnostic arbitrary word',oracle_origin=origin,reset_needs=()) for i in range(case_count))
    plan=c.ScenarioPlan(kind='ScenarioPlan',**common,contract=contract_ref,mandatory_requirement_ids=('echo',),scenarios=scenarios,
        seed_policy=c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(11,),same_cases_within_group=True))
    plan_ref=store.put_artifact(plan)
    adapter_ref=raw(adapter.encode(),'m4-worker-adapter')
    cases=[]
    for i in range(case_count):
        inp={'version':'m4-input-v1','scenario_id':'s'+str(i),'requirement_ids':['echo'],'fields':[
            {'name':'word','domain':{'kind':'choice','values':['indigo','saffron','cobalt','amber']}}]}
        comparison={'version':'m4-comparison-v1','scenario_id':'s'+str(i),'requirement_ids':['echo'],'mode':'json',
            'timeout_seconds':2.0,'observations':[{'name':'output','type':'string'},{'name':'exit','type':'integer'}],
            'assertions':[{'assertion_id':'output','requirement_ids':['echo'],'oracle_origin':origin.model_dump(mode='json'),
                'actual':'output','operator':'equal','expected':{'kind':'input','name':'word','suffix':'\n'}},
                {'assertion_id':'exit','requirement_ids':['echo'],'oracle_origin':origin.model_dump(mode='json'),
                'actual':'exit','operator':'equal','expected':{'kind':'literal','value':0}}]}
        cases.append(c.CaseDefinition(case_id='c'+str(i),requirement_ids=('echo',),inputs=raw(inp,'m4-case-input'),
            comparison=raw(comparison,'m4-case-comparison'),mandatory=True))
    verifier=c.VerifierBundle(kind='VerifierBundle',**common,contract=contract_ref,scenario_plan=plan_ref,cases=tuple(cases),
        completion_manifest=tuple(x.case_id for x in cases),worker_adapter=c.WorkerAdapter(code=adapter_ref,version='m4-worker-v1',
            supported_observables=('json',),limitations=('diagnostic only',)),public_examples=(),controls=(),
        permissions=c.VerifierPermissions(controller_role=c.ActorRole.CONTROLLER,worker_inputs=(adapter_ref,),output_limit_bytes=65536,submission_policy=rules))
    verifier_ref=store.put_artifact(verifier)
    if baseline is None:
        baseline=raw(SourceArchive({'src/click/__init__.py':SourceFile(b'# diagnostic',False)}).to_tar(),'source-archive',c.Visibility.AUTHORING)
    if environment is None:
        command=c.CommandSpec(argv=('python','--version'),working_directory='/workspace',timeout_seconds=1.0)
        environment=store.put_artifact(c.EnvironmentRecipe(kind='EnvironmentRecipe',**common,image_digest='example@sha256:'+'a'*64,
            interpreter_version='3.12.14',dependencies=(),setup=(command,),reset=(command,),services=(),limits=limits,
            neutral_repairs=(),locale='C.UTF-8',timezone='UTC',environment=(),randomness=plan.seed_policy,network_policy='none',baseline=baseline))
    # No real private SourcePair/H is inspected or loaded by this fixture.
    source_pair=c.ArtifactRef(sha256='0'*64,kind='SourcePair',schema_version=2,visibility=c.Visibility.PRIVATE,encoding='json')
    task=c.TaskBundle(kind='TaskBundle',**common,state=c.TaskState.BUILT,partition=c.Partition.DEVELOPMENT,
        repository_family='diagnostic-click',request_lineage=('diagnostic',),source_pair=source_pair,baseline=baseline,
        solver_view=c.SolverView(instruction=source,workspace=source,public_checks=(),runtime_manifest=source,inventory=source),
        contract=contract_ref,environment=environment,adapter_version=verifier.worker_adapter.version,private_oracle=verifier_ref,
        reference_solution=raw(b'No reference used','diagnostic-no-reference'),qualification=None)
    return store.put_artifact(task)


def replace_artifact(store,ref,**updates):
    old=store.get_artifact(ref)
    payload=old.model_dump(mode='json')
    payload.update({k:v.model_dump(mode='json') if hasattr(v,'model_dump') else v for k,v in updates.items()})
    return store.put_artifact(type(old).model_validate_json(json.dumps(payload)))
