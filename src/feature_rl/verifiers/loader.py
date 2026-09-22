"""Read frozen commands and expected outputs without loading or executing H."""
from dataclasses import dataclass
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import (TaskBundle, RequirementContract, VerifierBundle,
    EnvironmentRecipe, ArtifactRef, Visibility, ActorRole)
from .models import (BehavioralInput, ProcessObservation, CaseManifest, RealizedCase,
    ReferenceValidation, MAX_OUTPUT_BYTES)
from .language import decode_json

PRIVATE = {Visibility.PRIVATE, Visibility.EVALUATION}
OUTPUT_RECORD_CAP = MAX_OUTPUT_BYTES * 2 + 4096


def read_bytes(store, ref, cap, kind=None, private=False):
    ref = ArtifactRef.model_validate(ref)
    if kind is not None and ref.kind != kind: raise ValueError('unexpected byte artifact kind')
    if private and ref.visibility not in PRIVATE: raise ValueError('private artifact required')
    return store.get_bytes(ref, max_envelope_bytes=4*((cap+2)//3)+4096, max_payload_bytes=cap)


def read_local(store, ref, model, kind, cap=262144):
    value = decode_json(read_bytes(store, ref, cap, kind, private=True), cap)
    return model.model_validate_json(canonical_json(value))


def read_observation(store, ref):
    return read_local(store, ref, ProcessObservation, 'reference-output', OUTPUT_RECORD_CAP)


@dataclass(frozen=True)
class LoadedVerifier:
    task_ref: ArtifactRef
    task: TaskBundle
    contract: RequirementContract
    verifier: VerifierBundle
    recipe: EnvironmentRecipe
    inputs: tuple[BehavioralInput, ...]
    expected: tuple[ProcessObservation, ...]


def _artifact(store, ref, cls):
    value = store.get_artifact(ref, max_envelope_bytes=1024*1024)
    if type(value) is not cls: raise ValueError('wrong artifact type')
    return value


def load_verifier(store, task_ref):
    task = _artifact(store, task_ref, TaskBundle)
    verifier = _artifact(store, task.private_oracle, VerifierBundle)
    if task.adapter_version != 'behavioral-command-v1' or task.source_pair != verifier.source_pair:
        raise ValueError('task/verifier binding mismatch')
    contract, recipe, inputs, expected = validate_verifier_bundle(
        store, verifier, contract_ref=task.contract, environment=task.environment, baseline=task.baseline)
    return LoadedVerifier(task_ref, task, contract, verifier, recipe, inputs, expected)


def validate_verifier_bundle(store, verifier, *, contract_ref, environment, baseline):
    verifier = VerifierBundle.model_validate(verifier)
    contract = _artifact(store, contract_ref, RequirementContract)
    recipe = _artifact(store, environment, EnvironmentRecipe)
    if verifier.contract != contract_ref or recipe.baseline != baseline:
        raise ValueError('contract/baseline/recipe mismatch')
    from feature_rl.submission.source import validate_rules
    from feature_rl.environments import SourceRejected
    try:validate_rules(contract.allowed_changes)
    except SourceRejected as exc:raise ValueError(str(exc)) from exc
    for name in ('wall_seconds','cpu_seconds','memory_bytes','pids','disk_bytes','output_bytes'):
        if getattr(recipe.limits,name)>getattr(contract.episode_limits,name):
            raise ValueError('runtime recipe exceeds contract '+name)
    permissions = verifier.permissions
    if (permissions.controller_role != ActorRole.CONTROLLER
            or permissions.submission_policy != contract.allowed_changes):
        raise ValueError('verifier permission mismatch')
    if not 1 <= len(verifier.cases) <= 64: raise ValueError('case count limit')
    if permissions.output_limit_bytes > min(recipe.limits.output_bytes, contract.episode_limits.output_bytes, MAX_OUTPUT_BYTES):
        raise ValueError('output budget mismatch')
    ids = {r.requirement_id for r in contract.requirements + contract.compatibility_obligations}
    inputs = []; expected = []
    for case in verifier.cases:
        if not case.mandatory or not set(case.requirement_ids) <= ids:
            raise ValueError('unknown or optional behavioral case')
        inp = read_local(store, case.inputs, BehavioralInput, 'behavioral-input')
        if inp.command.timeout_seconds > min(recipe.limits.wall_seconds, contract.episode_limits.wall_seconds):
            raise ValueError('command timeout exceeds runtime/episode limit')
        inputs.append(inp)
        expected.append(read_observation(store, case.expected))
        if inp.mode == 'tests' and expected[-1].exit_code != 0:
            raise ValueError('H must pass repository tests')
    return contract, recipe, tuple(inputs), tuple(expected)


def validate_reference(store, verifier, environment, baseline, reference):
    from .inputs import matches
    proof = read_local(store, verifier.validation, ReferenceValidation, 'reference-validation', 1024*1024)
    if (proof.source_pair != verifier.source_pair or proof.environment != environment
            or proof.inputs != tuple(c.inputs for c in verifier.cases)
            or proof.expected != tuple(c.expected for c in verifier.cases)
            or proof.baseline.source != baseline or proof.reference.source != reference
            or proof.repeated_reference.source != reference):
        raise ValueError('construction evidence binding mismatch')
    count = len(verifier.cases)
    for run in (proof.baseline, proof.reference, proof.repeated_reference):
        if len(run.outputs) != count or len(run.executions) != count:
            raise ValueError('incomplete construction run')
    if proof.reference.outputs != proof.expected:
        raise ValueError('expected outputs differ from captured H')
    baseline_matches = []
    for case, actual, repeated in zip(verifier.cases, proof.baseline.outputs, proof.repeated_reference.outputs):
        inp = read_local(store, case.inputs, BehavioralInput, 'behavioral-input')
        expected = read_observation(store, case.expected)
        if not matches(inp, read_observation(store, repeated), expected):
            raise ValueError('H result changed on repeat')
        baseline_matches.append(matches(inp, read_observation(store, actual), expected))
    if tuple(baseline_matches) != proof.baseline_matches or all(baseline_matches):
        raise ValueError('baseline must fail at least one feature input')
    return proof


def materialize_manifest(checked, seed):
    return CaseManifest(task=checked.task_ref, verifier=checked.task.private_oracle, case_seed=seed,
        cases=tuple(RealizedCase(case_id=case.case_id, requirement_ids=case.requirement_ids,
            input_plan=case.inputs, expected=case.expected, mandatory=True) for case in checked.verifier.cases))
