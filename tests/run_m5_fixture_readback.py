"""Read-only M5 validation of retained TEST-only Docker receipts; never rerun Docker."""
from m4_fixtures import runtime_policy
import copy
import json
from pathlib import Path
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import EnvironmentRuntime,SandboxPolicy
from feature_rl.grading import GradingService
from feature_rl.registry import Registry
from feature_rl.verifiers import load_verifier,materialize_manifest
from feature_rl.verifiers.loader import read_local,read_bytes
from feature_rl.environments.models import Ownership
from feature_rl.qualification import RunBinding,QualificationRejected
from feature_rl.qualification.evidence import validate_grade,validate_reset,_assert_case_observation


def main():
    receipt=json.loads(Path('docs/evidence/M6/fixture-runtime/receipt.json').read_bytes())
    assert receipt['generated_feature'] is False and receipt['human_approval'] is False
    state=Path(receipt['state']).resolve()
    state.relative_to(Path('.feature-rl/research/M6').resolve())
    store=ArtifactStore(state/'store',c.ActorRole.CONTROLLER);registry=Registry(state/'registry',store)
    events=registry.events(limit=1000)
    context=receipt['context'];checked=load_verifier(store,c.ArtifactRef.model_validate_json(json.dumps(context['task'])))
    runtime=object.__new__(EnvironmentRuntime)
    runtime.store=store;runtime.policy=runtime_policy();runtime.revision=context['runtime_revision']
    # No engine exists on this read-only object: worker execution cannot occur.
    grader=GradingService(store=store,runtime=runtime,revision=context['grading_revision'])
    seen=set();checked_runs=0;resets=0;negative_checks=0
    for raw in receipt['qualification']['summary']['bindings']:
        ref=c.ArtifactRef.model_validate_json(json.dumps(raw));binding=read_local(store,ref,RunBinding,'m5-run-binding')
        result=registry.job(binding.grade_job).result
        grade,ids=validate_grade(store,checked,binding.submission,binding.seed,result,grader,seen=seen)
        assert ids==binding.operation_ids
        checked_runs+=1
        try:validate_grade(store,checked,binding.submission,binding.seed,result,grader,seen=seen)
        except QualificationRejected:negative_checks+=1
        else:raise AssertionError('replayed grade accepted as independent')
        if binding.reset:
            validate_reset(store,checked,binding.projection,binding.reset,grader,seen=seen);resets+=1
            try:validate_reset(store,checked,binding.projection,binding.reset,grader,seen=seen)
            except QualificationRejected:negative_checks+=1
            else:raise AssertionError('replayed interruption accepted')
        case=grade.cases[0]
        manifest=materialize_manifest(checked,binding.seed)
        value=json.loads(read_bytes(store,case.evidence,32*1024*1024,'environment-execution',True))
        owner=Ownership.model_validate_json(json.dumps(value['record']))
        for change in ('stdin_sha256','stdout_b64'):
            drift=copy.deepcopy(value)
            row=next(r for r in drift['commands'] if r.get('argv',[])[-3:]==['/usr/local/bin/python','-c',checked.adapter.decode()])
            row[change]='0'*64 if change=='stdin_sha256' else ''
            try:_assert_case_observation(checked,case,manifest.cases[0],checked.comparisons[0],drift,owner)
            except QualificationRejected:negative_checks+=1
            else:raise AssertionError('raw input/output drift accepted')
    assert (checked_runs,resets,negative_checks)==(9,3,30)
    assert registry.events(limit=1000)==events
    print(json.dumps({'label':'Read-only validation of prior TEST-only execution; no new Docker, control, human or approval evidence',
        'grade_ledgers':checked_runs,'reset_ledgers':resets,'replay_and_raw_input_output_rejections':negative_checks,
        'registry_events_unchanged':True,'status':'PASS'},sort_keys=True))


if __name__=='__main__':main()
