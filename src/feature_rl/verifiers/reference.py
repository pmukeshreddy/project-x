"""Construction only: run original tests or freeze explicit H command outputs."""
from datetime import datetime, timezone
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from .inputs import repository_tests, execution_request, observe, matches, NoBehavioralInputs
from .models import BehavioralInput, CapturedRun, ReferenceValidation, MAX_OUTPUT_BYTES
from .loader import read_local, read_observation


def freeze_reference(*, store, runtime, source_pair, contract_ref, prepared, revision, behavioral_inputs=()):
    pair = store.get_artifact(source_pair)
    contract = store.get_artifact(contract_ref)
    recipe = runtime.recipe(prepared)
    if pair.baseline != recipe.baseline:
        raise ValueError('source pair does not match runtime baseline')
    timeout = min(60.0, recipe.limits.wall_seconds, contract.episode_limits.wall_seconds)
    inputs = ()
    if getattr(runtime.profile, 'import_modules', ()):
        inputs = repository_tests(store, runtime.source(pair.baseline), runtime.source(pair.reference), source_pair, timeout)
    if not inputs: inputs = behavioral_inputs
    if not inputs: raise NoBehavioralInputs('No directly runnable PR tests or explicit behavioral command supplied')
    if len(inputs)>64: raise NoBehavioralInputs('behavioral input count exceeds bound')
    selected = tuple(read_local(store, ref, BehavioralInput, 'behavioral-input') for ref in inputs)
    if any(inp.command.timeout_seconds>timeout for inp in selected):
        raise NoBehavioralInputs('explicit command exceeds construction time bound')
    try:
        requests = tuple(execution_request(store, inp, runtime.policy) for inp in selected)
    except ValueError as exc:
        raise NoBehavioralInputs('behavioral input cannot be staged: '+str(exc)) from exc
    cap = min(MAX_OUTPUT_BYTES, recipe.limits.output_bytes, contract.episode_limits.output_bytes)
    def save(value, kind):
        return store.put_bytes(canonical_json(value.model_dump(mode='json')), kind, c.Visibility.PRIVATE)
    costs = []
    def run(source, role):
        handle = runtime.open_workspace(prepared, source=source, role=role,
            source_pair=source_pair if role == 'reference' else None, allowed_changes=contract.allowed_changes)
        try:
            build = runtime.build_snapshot(handle); costs.append(build.cost)
            outputs = []; executions = []
            for inp, request in zip(selected, requests):
                output = runtime.execute(handle, request, build=build)
                costs.append(output.cost); executions.append(output.evidence)
                observed = observe(output, cap)
                if inp.mode == 'tests' and observed.exit_code not in ({0} if role == 'reference' else {0, 1}):
                    raise NoBehavioralInputs(role+' cannot run the original PR tests successfully; inspect '+output.evidence.sha256)
                outputs.append(save(observed, 'reference-output'))
            return CapturedRun(source=source, build=build.evidence, executions=tuple(executions),
                outputs=tuple(outputs), cleanup_verified=True)
        finally:
            runtime.close(handle)
    reference = run(pair.reference, 'reference')
    repeated = run(pair.reference, 'reference')
    expected = tuple(read_observation(store, ref) for ref in reference.outputs)
    if not all(matches(inp, read_observation(store, ref), gold) for inp, ref, gold in zip(selected, repeated.outputs, expected)):
        raise NoBehavioralInputs('H results are not deterministic across clean executions')
    baseline = run(pair.baseline, 'baseline')
    baseline_matches = tuple(matches(inp, read_observation(store, ref), gold)
        for inp, ref, gold in zip(selected, baseline.outputs, expected))
    if all(baseline_matches): raise NoBehavioralInputs('B already passes all available behavioral inputs')
    proof = ReferenceValidation(source_pair=source_pair, environment=prepared.recipe,
        inputs=inputs, expected=reference.outputs, baseline=baseline, reference=reference,
        repeated_reference=repeated, baseline_matches=baseline_matches, costs=tuple(costs))
    validation = save(proof, 'reference-validation')
    feature_ids = tuple(r.requirement_id for r in contract.requirements if r.mandatory)
    if not feature_ids: raise NoBehavioralInputs('no mandatory feature requirement')
    now = datetime.now(timezone.utc)
    evidence = c.EvidenceRecord(producer='feature_rl.verifiers.reference', command=('freeze_reference',),
        recorded_at=now, exit_status=0, artifacts=(validation,), revision=revision, scope='real_integration')
    verifier = c.VerifierBundle(kind='VerifierBundle', schema_version=2, visibility=c.Visibility.PRIVATE,
        provenance=c.Provenance(producer='feature_rl.verifiers.reference', producer_version=revision,
            created_at=now, inputs=(source_pair, contract_ref, prepared.recipe, validation), evidence=(evidence,)),
        costs=tuple(costs), contract=contract_ref, source_pair=source_pair, validation=validation,
        cases=tuple(c.CaseDefinition(case_id='case_'+str(index), requirement_ids=feature_ids,
            inputs=inp, expected=expected, mandatory=True) for index, (inp, expected) in enumerate(zip(inputs, reference.outputs), 1)),
        completion_manifest=tuple('case_'+str(index) for index in range(1, len(inputs)+1)),
        public_examples=contract.public_checks,
        permissions=c.VerifierPermissions(controller_role=c.ActorRole.CONTROLLER,
            output_limit_bytes=cap, submission_policy=contract.allowed_changes))
    ref = store.put_artifact(verifier)
    return c.OperationResult(operation='construct', disposition=c.Disposition.SUCCESS, artifacts=(ref,),
        evidence=(evidence,), costs=tuple(costs), reason='H passes and repeats; B builds and fails the feature check')
