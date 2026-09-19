"""Mechanical frozen-artifact joins, not semantic qualification or admission."""
from dataclasses import dataclass
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import (TaskBundle, RequirementContract, ScenarioPlan, VerifierBundle,
    EnvironmentRecipe, ArtifactRef, Visibility, ActorRole)
from feature_rl.submission.source import validate_rules
from feature_rl.environments import SourceRejected
from .models import InputPlan, CaseComparison, CaseManifest, RealizedCase, unique, Constant, Choice
from .language import decode_json, realize_inputs, check_value

PRIVATE={Visibility.PRIVATE,Visibility.EVALUATION}


def read_bytes(store,ref,cap,kind=None,private=False):
    ref=ArtifactRef.model_validate(ref)
    if kind is not None and ref.kind!=kind:raise ValueError('unexpected byte artifact kind')
    if private and ref.visibility not in PRIVATE:raise ValueError('private artifact required')
    return store.get_bytes(ref,max_envelope_bytes=4*((cap+2)//3)+4096,max_payload_bytes=cap)


def read_local(store,ref,model,kind,cap=262144):
    data=read_bytes(store,ref,cap,kind,private=True)
    value=decode_json(data,cap)
    return model.model_validate_json(canonical_json(value))


@dataclass(frozen=True)
class LoadedVerifier:
    task_ref: ArtifactRef
    task: TaskBundle
    contract: RequirementContract
    plan: ScenarioPlan
    verifier: VerifierBundle
    recipe: EnvironmentRecipe
    adapter: bytes
    inputs: tuple[InputPlan,...]
    comparisons: tuple[CaseComparison,...]


def _artifact(store,ref,cls):
    value=store.get_artifact(ref,max_envelope_bytes=1024*1024)
    if type(value) is not cls:raise ValueError('wrong artifact type')
    return value


def _validate_operands(assertion,inp,cmp):
    kinds={o.name:o.type for o in cmp.observations}
    actual=kinds[assertion.actual];expected=assertion.expected
    if expected.kind=='observation':
        other=kinds[expected.name]
        if assertion.operator=='equal' and other==actual:return
        if assertion.operator=='contains' and actual==other=='string':return
        if assertion.operator=='member' and other==actual+'_list':return
        raise ValueError('incompatible observation comparison types')
    if expected.kind=='literal':values=[expected.value]
    else:
        domain=next(f.domain for f in inp.fields if f.name==expected.name)
        values=[domain.value] if isinstance(domain,Constant) else list(domain.values) if isinstance(domain,Choice) else [domain.low,domain.high]
        if expected.prefix or expected.suffix:
            if any(type(v) is not str for v in values):raise ValueError('input text template requires strings')
            values=[expected.prefix+v+expected.suffix for v in values]
    for value in values:
        # The strict transport turns arrays into tuples. check_value accepts the
        # corresponding ordinary JSON list, without Boolean/integer coercion.
        ordinary=list(value) if isinstance(value,tuple) else value
        if assertion.operator=='equal':valid=check_value(ordinary,actual)
        elif assertion.operator=='contains':valid=actual=='string' and type(value) is str and bool(value)
        else:valid=actual in {'string','integer','boolean','null'} and isinstance(value,tuple) and bool(value) and all(check_value(v,actual) for v in value)
        if not valid:raise ValueError('incompatible or empty comparison operand')


def load_verifier(store,task_ref):
    task=_artifact(store,task_ref,TaskBundle)
    contract=_artifact(store,task.contract,RequirementContract)
    verifier=_artifact(store,task.private_oracle,VerifierBundle)
    plan=_artifact(store,verifier.scenario_plan,ScenarioPlan)
    recipe=_artifact(store,task.environment,EnvironmentRecipe)
    if task.contract!=verifier.contract or plan.contract!=task.contract:raise ValueError('contract reference mismatch')
    if recipe.baseline!=task.baseline:raise ValueError('baseline/recipe mismatch')
    # M3 enforces this fixed worker envelope, not per-task overrides. A lower
    # contract grant cannot be implemented by merely shortening the probe command.
    # Token/tool budgets belong to the solver, so they are not worker resources.
    for name in ('wall_seconds','cpu_seconds','memory_bytes','pids','disk_bytes','output_bytes'):
        if getattr(recipe.limits,name)>getattr(contract.episode_limits,name):
            raise ValueError('runtime recipe exceeds contract '+name)
    if task.adapter_version!=verifier.worker_adapter.version:raise ValueError('adapter version mismatch')
    if verifier.worker_adapter.version!='m4-worker-v1':raise ValueError('unsupported adapter version')
    if verifier.permissions.controller_role!=ActorRole.CONTROLLER:raise ValueError('controller runtime required')
    if verifier.permissions.submission_policy!=contract.allowed_changes:raise ValueError('submission policy mismatch')
    try:validate_rules(contract.allowed_changes)
    except SourceRejected as exc:raise ValueError('unsupported contract submission policy: '+str(exc)) from exc
    if verifier.permissions.worker_inputs!=(verifier.worker_adapter.code,):raise ValueError('worker allowlist must contain adapter alone')
    if not 1<=len(verifier.cases)<=256:raise ValueError('case count limit')
    if verifier.permissions.output_limit_bytes>min(recipe.limits.output_bytes,contract.episode_limits.output_bytes,2*1024*1024):
        raise ValueError('output budget mismatch')
    if plan.seed_policy.algorithm!='m4-sha256-v1' or not plan.seed_policy.same_cases_within_group:
        raise ValueError('unsupported seed policy')
    unique(plan.mandatory_requirement_ids,'mandatory IDs')
    requirements={r.requirement_id:r for r in contract.requirements+contract.compatibility_obligations}
    mandatory={k for k,v in requirements.items() if v.mandatory}
    if set(plan.mandatory_requirement_ids)!=mandatory:raise ValueError('mandatory requirement set mismatch')
    scenarios={s.scenario_id:s for s in plan.scenarios}
    for scenario in plan.scenarios:
        unique(scenario.requirement_ids,'scenario requirements')
        if not set(scenario.requirement_ids)<=requirements.keys():raise ValueError('unknown scenario requirement')
        if scenario.reset_needs:raise ValueError('stateful/reset semantics unsupported by initial adapter')
    inputs=[];comparisons=[];covered=set();seen_scenarios=set();total=0
    for case in verifier.cases:
        unique(case.requirement_ids,'case requirements')
        inp=read_local(store,case.inputs,InputPlan,'m4-case-input')
        cmp=read_local(store,case.comparison,CaseComparison,'m4-case-comparison')
        total+=len(inp.model_dump_json())+len(cmp.model_dump_json())
        if total>8*1024*1024:raise ValueError('aggregate checker byte cap')
        if inp.scenario_id!=cmp.scenario_id or inp.scenario_id not in scenarios:raise ValueError('scenario join mismatch')
        scenario=scenarios[inp.scenario_id]
        ids=set(case.requirement_ids)
        if ids!=set(inp.requirement_ids) or ids!=set(cmp.requirement_ids) or not ids<=set(scenario.requirement_ids):
            raise ValueError('case requirement join mismatch')
        if case.mandatory!=bool(ids&mandatory):raise ValueError('mandatory case downgraded or invented')
        if cmp.timeout_seconds>min(recipe.limits.wall_seconds,contract.episode_limits.wall_seconds):raise ValueError('case timeout exceeds declared bound')
        if cmp.mode not in verifier.worker_adapter.supported_observables:raise ValueError('unsupported adapter observable')
        assertion_ids=set()
        for assertion in cmp.assertions:
            if not set(assertion.requirement_ids)<=ids:raise ValueError('unknown assertion requirement')
            assertion_ids.update(assertion.requirement_ids)
            if assertion.oracle_origin!=scenario.oracle_origin:raise ValueError('assertion oracle origin mismatch')
            if assertion.expected.kind=='input' and assertion.expected.name not in {f.name for f in inp.fields}:
                raise ValueError('unknown expected input')
            _validate_operands(assertion,inp,cmp)
        if assertion_ids!=ids:raise ValueError('unasserted case requirements')
        if case.mandatory:covered.update(ids&mandatory)
        inputs.append(inp);comparisons.append(cmp);seen_scenarios.add(scenario.scenario_id)
    # Every declared family is retained, including nonmandatory diagnostic families.
    if seen_scenarios!=scenarios.keys() or covered!=mandatory:raise ValueError('incomplete scenario/requirement coverage')
    adapter=read_bytes(store,verifier.worker_adapter.code,65536,'m4-worker-adapter',private=True)
    if not adapter or b'\x00' in adapter:raise ValueError('empty or invalid adapter source')
    adapter.decode('utf-8')  # Inert text only. Do not compile/import it on this host.
    return LoadedVerifier(task_ref,task,contract,plan,verifier,recipe,adapter,tuple(inputs),tuple(comparisons))


def materialize_manifest(checked,seed):
    cases=[]
    for case,inp in zip(checked.verifier.cases,checked.inputs):
        cases.append(RealizedCase(case_id=case.case_id,scenario_id=inp.scenario_id,requirement_ids=case.requirement_ids,
            inputs=realize_inputs(inp,case.case_id,seed),input_plan=case.inputs,comparison=case.comparison,mandatory=case.mandatory))
    return CaseManifest(version='m4-manifest-v1',task=checked.task_ref,verifier=checked.task.private_oracle,
        case_seed=seed,algorithm='m4-sha256-v1',cases=tuple(cases))
