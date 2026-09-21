"""Controller decisions for the small environment factory."""
import pytest
from feature_rl import contracts as c
from feature_rl.pipeline.workflow import checker_output_limit, validate_seed_policy, wrong_implementations


@pytest.mark.parametrize('runtime,episode,expected',[(4*1024*1024,4*1024*1024,2*1024*1024),(4*1024*1024,65536,65536),(32768,65536,32768)])
def test_checker_cap_fits_runtime_episode_and_finalizer(runtime,episode,expected):
    assert checker_output_limit(runtime,episode)==expected


def test_unsupported_seed_algorithm_fails_before_external_work():
    with pytest.raises(ValueError,match='m4-sha256-v1'):
        validate_seed_policy(c.SeedPolicy(algorithm='PCG64',seeds=(11,),same_cases_within_group=True))


@pytest.mark.parametrize('compatibility', [False, True])
def test_small_fixed_set_of_wrong_implementations(compatibility):
    assert [kind for kind,_ in wrong_implementations(compatibility)] == ['partial','happy_path','hardcoded'] + (['regression'] if compatibility else [])


def test_scenario_identity_oracle_and_runtime_capabilities_are_controller_owned(tmp_path):
    from feature_rl.scenarios import build_scenario_plan
    from test_checker_authoring import checker_fixture
    store, _, _, inputs, *_ = checker_fixture(tmp_path)
    contract = store.get_artifact(inputs.contract)
    plan = build_scenario_plan(contract, inputs.contract,
        c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(11,),same_cases_within_group=True),
        provenance=inputs.provenance, costs=inputs.costs)
    assert len(plan.scenarios) == len(contract.requirements+contract.compatibility_obligations)
    for index,(scenario,requirement) in enumerate(zip(plan.scenarios,contract.requirements+contract.compatibility_obligations),1):
        assert scenario.scenario_id == f'scenario_{index}'
        assert scenario.requirement_ids == (requirement.requirement_id,)
        assert scenario.oracle_origin == requirement.evidence[0]
        assert scenario.observations == (requirement.observable,)
        assert not scenario.reset_needs


def test_workflow_authors_one_checker_and_passes_frozen_artifacts_to_builder(tmp_path, monkeypatch):
    """Exercise orchestration/finalizers with synthetic semantic output, no external work."""
    from types import SimpleNamespace
    from feature_rl.environments import SandboxPolicy, SourceArchive
    from feature_rl.pipeline.workflow import FeatureWorkflow
    from feature_rl.requirements import ContractFinalizationInputs, AuthoringEvidenceResolver
    from feature_rl.verifiers import (CheckerFinalizationInputs, CheckerFinalizer,
        ControlFinalizer, ControlProposal, SourceChange, TextReplacement)
    from feature_rl.verifiers.behavioral import BehavioralSpecification, compile_behavioral
    from test_factory_authoring import setup
    from test_checker_authoring import checker_fixture
    from test_behavioral_spec import specification
    from test_generation import limits

    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    store = factory.store
    contract = store.get_artifact(base.inputs.contract)
    policy = SandboxPolicy.model_validate_json(store.get_bytes(base.environment.policy))
    workflow = object.__new__(FeatureWorkflow)
    workflow.factory, workflow.store = factory, store
    workflow.registry = SimpleNamespace(assert_usable=lambda ref: None, register=lambda ref: None)
    workflow.configuration = base.inputs.contract
    workflow.runtime = SimpleNamespace(policy=policy, bind_policy=lambda value: None)
    workflow.settings = SimpleNamespace(generation_limits=limits())
    workflow._validated = lambda claim: (SimpleNamespace(state='running'), None, None)
    workflow._claimed_at = lambda claim: base.inputs.provenance.created_at
    workflow._author_factory = lambda selected: factory
    workflow._sources = lambda *args: (base.sources, base.resolver,
        SourceArchive.read(store.get_bytes(base.inputs.baseline), policy))
    workflow._finish = lambda claim, ref, result: result
    selected = SimpleNamespace(candidate=candidate, source_pair=base.source_pair, baseline=base.inputs.baseline,
        provenance_label=contract.provenance_label, model_dump=lambda **kwargs: {})
    prepared = SimpleNamespace(environment=base.environment, context=base.sources[-1], entry_points=contract.entry_points,
        supported_observables=tuple(r.observable for r in contract.requirements))
    request = SimpleNamespace(seed_policy=c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(11,),same_cases_within_group=True),
        allowed_requirement_ids=('echo',), public_checks=(), episode_limits=contract.episode_limits,
        invocation='unit-workflow', model_dump=lambda **kwargs: {})
    success = lambda ref: SimpleNamespace(disposition=c.Disposition.SUCCESS, artifacts=(ref,))
    captured = []
    def step(claim, ref, phase, inputs, execute, **kwargs):
        if phase == 'intake': return selected, None
        if phase == 'source': return success(candidate), None
        if phase == 'preparation': return prepared, None
        assert phase == 'construction'
        captured.append(inputs['inputs'])
        return success(base.inputs.contract), None
    workflow._step = step
    _, _, compiled_fixture, *_ = checker_fixture(tmp_path/'semantic')
    authored = []
    def author(claim, ref, owner, selected, prepared, resolver, sources, inputs, make_request):
        authored.append(type(inputs))
        generation = make_request(0)
        assert all(context.role != 'reference' for context in generation.contexts)
        if isinstance(inputs, ContractFinalizationInputs): return success(base.inputs.contract)
        evidence = AuthoringEvidenceResolver(store=store, **resolver.model_dump())
        if isinstance(inputs, CheckerFinalizationInputs):
            plan = store.get_artifact(inputs.scenario_plan)
            spec = BehavioralSpecification(scenarios=specification(compiled_fixture).scenarios[:len(plan.scenarios)])
            proposal = compile_behavioral(spec, contract, plan, timeout_seconds=5.0)
            return success(CheckerFinalizer(store=store,resolver=evidence).prepare(proposal,inputs,sources).publish(store))
        proposal = ControlProposal(files=(SourceChange(path='src/click/__init__.py', replacements=(
            TextReplacement(before='# diagnostic',after='# '+inputs.category),)),),deletions=(),rationale=inputs.category)
        return success(ControlFinalizer(store=store,resolver=evidence).prepare(proposal,inputs,sources).publish(store))
    workflow._author = author
    workflow._execute(SimpleNamespace(job_id='1'*64), base.inputs.contract, request)
    assert authored.count(CheckerFinalizationInputs) == 1
    assert len(authored) == 5  # contract + three wrong implementations + checker
    assert len(captured) == 1
    verifier = store.get_artifact(c.ArtifactRef.model_validate_json(__import__('json').dumps(captured[0]['verifier'])))
    assert [control.category for control in verifier.controls] == ['partial','happy_path','hardcoded']
    assert not runner.calls
