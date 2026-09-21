"""Controller prerequisites must agree with actual M4 loader limits."""
import pytest
from feature_rl import contracts as c
from feature_rl.pipeline.workflow import checker_output_limit, validate_seed_policy


@pytest.mark.parametrize('runtime,episode,expected',[
    (4*1024*1024,4*1024*1024,2*1024*1024),
    (4*1024*1024,65536,65536),
    (32768,65536,32768),
])
def test_checker_cap_fits_runtime_episode_and_finalizer(runtime,episode,expected):
    from feature_rl.verifiers.authoring_models import CheckerFinalizationInputs
    from pydantic import TypeAdapter
    value = checker_output_limit(runtime, episode)
    field = CheckerFinalizationInputs.model_fields['output_limit_bytes']
    assert TypeAdapter(field.rebuild_annotation()).validate_python(value) == expected


def test_unsupported_seed_algorithm_fails_before_external_work():
    with pytest.raises(ValueError,match='m4-sha256-v1'):
        validate_seed_policy(c.SeedPolicy(algorithm='PCG64',seeds=(11,23,47),same_cases_within_group=True))
    validate_seed_policy(c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(11,23,47),same_cases_within_group=True))


@pytest.mark.parametrize('compatibility', [False, True])
def test_workflow_default_plan_covers_only_behavioral_controls(tmp_path, monkeypatch, compatibility):
    from types import SimpleNamespace
    from feature_rl.environments import SandboxPolicy
    from feature_rl.pipeline import workflow as module
    from feature_rl.requirements import ContractFinalizationInputs
    from feature_rl.scenarios import ScenarioFinalizationInputs
    from test_factory_authoring import setup
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    contract=factory.store.get_artifact(base.inputs.contract)
    if compatibility:
        contract=contract.model_copy(update={'compatibility_obligations':(
            contract.requirements[0].model_copy(update={'requirement_id':'preserved'}),)})
    policy=SandboxPolicy.model_validate_json(factory.store.get_bytes(base.environment.policy))
    selected=SimpleNamespace(candidate=candidate,source_pair=base.source_pair,
        baseline=base.inputs.baseline,provenance_label=contract.provenance_label,model_dump=lambda **kwargs:{})
    prepared=SimpleNamespace(environment=base.environment,context=base.sources[-1],
        entry_points=contract.entry_points,supported_observables=tuple(
            item.observable for item in contract.requirements))
    request=SimpleNamespace(seed_policy=c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(11,23,47),same_cases_within_group=True),
        allowed_requirement_ids=('echo','preserved'),public_checks=contract.public_checks,
        episode_limits=contract.episode_limits,model_dump=lambda **kwargs:{})
    workflow=object.__new__(module.FeatureWorkflow)
    workflow.factory,workflow.store,workflow.registry=factory,factory.store,SimpleNamespace(assert_usable=lambda ref:None)
    workflow.configuration=base.inputs.contract
    workflow.runtime=SimpleNamespace(policy=policy,bind_policy=lambda value:None)
    workflow.settings=SimpleNamespace(max_controls=32)
    workflow._validated=lambda claim:(SimpleNamespace(state='running'),None,None)
    success=lambda artifact:SimpleNamespace(disposition=c.Disposition.SUCCESS,artifacts=(artifact,))
    workflow._step=lambda claim,ref,phase,*args,**kwargs:({'intake':selected,
        'source':success(candidate),'preparation':prepared}[phase],None)
    workflow._sources=lambda *args:(base.sources,base.resolver,None)
    workflow._author_factory=lambda selected:factory
    workflow._provenance=lambda *args:base.inputs.provenance
    original_typed=module.typed
    monkeypatch.setattr(module,'typed',lambda store,ref,model:contract if ref==base.inputs.contract else original_typed(store,ref,model))
    class PlanCaptured(Exception):pass
    plans=[]
    def author(*args,**kwargs):
        inputs=args[7]
        if isinstance(inputs,ContractFinalizationInputs):return success(base.inputs.contract)
        if isinstance(inputs,ScenarioFinalizationInputs):return success(base.inputs.scenario_plan)
        plans.append(kwargs['plan'])
        raise PlanCaptured
    workflow._author=author
    with pytest.raises(PlanCaptured):workflow._execute(SimpleNamespace(),base.inputs.contract,request)
    expected=[('omission',('echo',))]
    if compatibility:expected.append(('omission',('preserved',)))
    expected.extend((category,('echo',)) for category in ('plausible_wrong','hardcoded'))
    if compatibility:expected.append(('regression',('preserved',)))
    expected.append(('alternative_positive',()))
    assert [(slot.category,slot.requirement_ids) for slot in plans[0].slots]==expected
    assert all(slot.attack is None for slot in plans[0].slots)
    assert not runner.calls
