"""Explicit synthetic retained-ledger fixtures; no runtime or model dispatch."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import authoring as a
from feature_rl.pipeline.packaging import document, read_record
from feature_rl.qualification import RepairHistory, RepairAttempt, QualificationPolicy, validate_repairs
from feature_rl.qualification.evidence import evidence, put_record
from feature_rl.qualification.models import RunBinding, QualificationSummary, QualificationRejected
from feature_rl.registry import JobSpec
from feature_rl.verifiers import load_verifier, materialize_manifest
from codex_fixtures import events, response
from test_factory_authoring import setup, repaired, bind_events
from test_factory_checker_fragments import fragment
from m5_fixtures import task_fixture


def retained_failure(tmp_path, monkeypatch, *, exhaust_default=False):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    if exhaust_default:
        assert factory.author(candidate, call=base).disposition == c.Disposition.SUCCESS
        for index in (1, 2):
            call = repaired(base, index)
            bind_events(runner, call.generation.request)
            assert factory.author(candidate, call=call).disposition == c.Disposition.SUCCESS
    # Three independent scenario lanes, all explicitly synthetic echo probes.
    plan = factory.store.get_artifact(base.inputs.scenario_plan)
    plan = plan.model_copy(update={'scenarios': tuple(plan.scenarios[0].model_copy(
        update={'scenario_id': f's{i}'}) for i in range(3))})
    plan_ref = factory.store.put_artifact(plan)
    base = base.model_copy(update={'inputs': base.inputs.model_copy(update={
        'scenario_plan': plan_ref, 'provenance': base.inputs.provenance.model_copy(update={
            'inputs': (*base.inputs.provenance.inputs, plan_ref)})})})
    refs = tuple(fragment(factory, base, runner, f's{i}') for i in range(3))
    old = a.jobs(factory, candidate)
    from feature_rl.verifiers.fragments import CheckerFragmentInputs
    replacements = tuple(repaired(author.call, 20+i) for i, (_, author) in enumerate(old)
        if isinstance(author.call.inputs, CheckerFragmentInputs))
    verifier = factory.assemble_checker(candidate, inputs=base.inputs, fragments=refs).artifacts[0]
    original = factory.store.get_artifact(task_fixture(factory.store))
    task = factory.store.put_artifact(original.model_copy(update={'source_pair': base.source_pair,
        'baseline': base.inputs.baseline, 'contract': base.inputs.contract,
        'environment': base.inputs.environment, 'private_oracle': verifier}))
    checked = load_verifier(factory.store, task)
    policy = put_record(factory.store, QualificationPolicy(), 'm5-qualification-policy')
    config = put_record(factory.store, {'version': 'm5-configuration-v1', 'policy': document(policy),
        'grading_revision': 'b'*40, 'runtime_revision': 'a'*40, 'builder_revision': factory.revision},
        'm5-qualification-configuration')
    job = factory.registry.enqueue(JobSpec(operation='qualify', inputs=(task,), configuration=config,
        implementation='c'*40, invocation='m5-qualify', attempt_limit=3))
    claim = factory.registry.claim(job.job_id, owner='synthetic-fixture', claim_key='qualification')
    manifest = materialize_manifest(checked, 11)
    from feature_rl.grading import GradeReceipt, CaseResult
    from feature_rl.environments.models import Ownership
    from feature_rl.grading.bootstrap import recipe_adapter_argv
    from feature_rl.submission import SubmissionService
    from feature_rl.environments import SourceArchive
    from m4_fixtures import runtime_policy
    submission = SubmissionService(store=factory.store, policy=runtime_policy()).create(checked.task.baseline,
        SourceArchive({}).to_tar(), (), checked.contract.allowed_changes)
    cases = []
    for index, case in enumerate(manifest.cases):
        owner = Ownership(operation_id=f'{index:032x}', owner_token='1'*32,
            daemon_id='synthetic', container_name=f'synthetic-{index}', container_id=f'unit-{index}',
            phase='removed', binding={}, saved_source={}, created_at='2026-09-20T00:00:00Z')
        stdin = canonical_json({'case_id': case.case_id, 'inputs': dict(case.inputs)})
        raw = {'phase': 'execute', 'record': document(owner), 'cleanup_verified': True,
            'commands': [{'argv': ['synthetic-docker', 'exec', owner.container_id, *recipe_adapter_argv(checked)],
                'stdin_bytes': len(stdin), 'stdin_sha256': hashlib.sha256(stdin).hexdigest(),
                'exit_code': 0, 'reason': 'exited', 'stdout_b64': base64.b64encode(b'invalid JSON').decode(),
                'stderr_b64': ''}], 'extra': {'error': None, 'failure_category': 'none', 'reason': 'completed'}}
        ref = put_record(factory.store, raw, 'environment-execution')
        cases.append(CaseResult(case_id=case.case_id, mandatory=case.mandatory, status='protocol_failure',
            passed=None, assertions=(), evidence=ref, reason='Synthetic invalid observation protocol'))
    marker = checked.task.provenance.evidence[0].artifacts[0]
    receipt = GradeReceipt(version='m4-grade-v1', task=task, submission=submission, verifier=verifier,
        case_seed=11, manifest=put_record(factory.store, manifest, 'm4-case-manifest'), source=checked.task.baseline,
        disposition=c.Disposition.REJECTED, reward=0, reason='Synthetic retained baseline failure',
        expected_case_ids=tuple(x.case_id for x in cases), cases=tuple(cases), build_evidence=marker,
        runtime_evidence=(marker, *(x.evidence for x in cases)), cleanup_verified=True,
        implementation_revision='b'*40, recorded_at=datetime(2026,9,20,tzinfo=timezone.utc))
    grade = put_record(factory.store, receipt, 'm4-grade-receipt')
    run_config = put_record(factory.store, {'version': 'm5-run-configuration-v2',
        'configuration': document(config), 'projection': document(marker), 'submission': document(submission),
        'source_dependencies': [document(checked.task.baseline)], 'seed': 11,
        'name': 'baseline_absence', 'mode': 'semantic_negative', 'targets': ['echo'], 'reset': False},
        'm5-run-configuration')
    run = factory.registry.enqueue(JobSpec(operation='grade', inputs=(task,), configuration=run_config,
        implementation='c'*40, invocation='m5-run:'+job.job_id+':baseline_absence', attempt_limit=3))
    run_claim = factory.registry.claim(run.job_id, owner='synthetic-fixture', claim_key='grade')
    result = c.OperationResult(operation='grade', disposition=c.Disposition.REJECTED, artifacts=(grade,),
        evidence=(evidence(grade, 'b'*40, ('Synthetic retained grading fixture',)),), costs=checked.task.costs,
        reason='Synthetic retained grading fixture')
    from feature_rl.qualification import QualificationService
    QualificationService._complete(factory, run_claim, result)
    binding = put_record(factory.store, RunBinding(name='baseline_absence', task=task,
        projection=marker, submission=submission, seed=11, grade=grade, mode='semantic_negative', targets=('echo',),
        operation_ids=tuple(f'{i:032x}' for i in range(3)), grade_job=run.job_id), 'm5-run-binding')
    ev = evidence(binding, 'c'*40, ('QualificationService.execute', 'baseline_absence', task.sha256), scope='real_integration')
    gate = c.RunAssessment(name='baseline_absence', subject=task, disposition=c.Disposition.REJECTED,
        passed=False, requirement_ids=('echo',), reason='Runnable negative must fail exactly its declared semantic targets', evidence=(ev,))
    issues = ('oracle_disagreement: baseline_absence: '+gate.reason,)
    summary = put_record(factory.store, QualificationSummary(task=task, policy=policy, projection=marker,
        bindings=(binding,), issues=issues, repair_count=0, qualification_job=job.job_id), 'm5-qualification-summary')
    report = c.QualificationReport(kind='QualificationReport', schema_version=1, visibility=c.Visibility.PRIVATE,
        provenance=c.Provenance(producer='feature_rl.qualification', producer_version='c'*40,
            created_at=ev.recorded_at, inputs=(task, policy, summary), evidence=(ev,)), costs=checked.task.costs,
        task=task, disposition=c.Disposition.REJECTED, baseline_health=None, baseline_absence=gate,
        reference_run=None, controls=(), fresh_runs=(), interrupted_reset_runs=(), rejection_reasons=issues,
        repair_attempts=0, policy_version='pilot-v1')
    report_ref = factory.store.put_artifact(report)
    QualificationService._complete(factory, claim, c.OperationResult(operation='qualify', disposition=c.Disposition.REJECTED,
        artifacts=(report_ref, summary), evidence=(ev,), costs=report.costs, reason=issues[0]))
    factory.registry.quarantine(task, notice_id='synthetic-observed-defect', reason=issues[0], evidence=(summary,))
    return factory, candidate, base, runner, replacements, report_ref, old


def test_exact_three_semantic_successors_remain_counted_and_single_use(tmp_path, monkeypatch):
    from feature_rl.pipeline.semantic_repairs import create_semantic_repair_authorization
    factory, candidate, base, runner, calls, report, old = retained_failure(tmp_path, monkeypatch, exhaust_default=True)
    auth = create_semantic_repair_authorization(factory, report=report, calls=calls,
        authorization='Synthetic unit authorization: repair these three retained checker fragments once.')
    factory.authoring = factory.authoring.model_copy(update={'semantic_repair_authorization': auth})
    original = {job.result.artifacts[-1]: factory.store.get_bytes(job.result.artifacts[-1]) for job, _ in old}
    results = []
    for call in calls:
        bind_events(runner, call.generation.request)
        results.append(factory.author(candidate, call=call))
    assert all(result.disposition == c.Disposition.SUCCESS for result in results)
    from feature_rl.pipeline.authoring_history import selected_history
    from feature_rl.pipeline.construction import ConstructionRequest
    from feature_rl.pipeline.models import BuildInputs
    assembled = factory.assemble_checker(candidate, inputs=base.inputs,
        fragments=tuple(result.artifacts[0] for result in results)).artifacts[0]
    build = BuildInputs(source_pair=base.source_pair, contract=base.inputs.contract,
        scenario_plan=base.inputs.scenario_plan, verifier=assembled, environment=base.environment,
        baseline_files=('src/click/__init__.py',), invocation='SYNTHETIC_AUTHORIZATION')
    history = read_record(factory.store, selected_history(factory, ConstructionRequest(candidate=candidate,
        source=factory.screen_source(candidate).artifacts[0], inputs=build, builder_job=None)),
        RepairHistory, 'm5-repair-history').model_copy(update={'complete': True})
    assert len(history.attempts) == 5 and auth in history.journal_refs
    assert validate_repairs(history, candidate, (), store=factory.store, registry=factory.registry,
        semantic_authorization=auth) == 5
    assert all(factory.store.get_bytes(ref) == raw for ref, raw in original.items())
    assert all(a.read_authoring_receipt(factory.store, result.artifacts[-1]).repair for result in results)
    with pytest.raises(QualificationRejected, match='authorization'):
        validate_repairs(history, candidate, (), store=factory.store, registry=factory.registry)
    with pytest.raises(ValueError, match='authorization|allowance|predecessor'):
        factory.author(candidate, call=repaired(calls[0], 99))
    assert len(runner.calls) == 9


def test_authorization_rejects_unselected_report_and_nonexact_requests(tmp_path, monkeypatch):
    from feature_rl.pipeline.semantic_repairs import create_semantic_repair_authorization
    factory, candidate, base, runner, calls, report, old = retained_failure(tmp_path, monkeypatch)
    forged = factory.store.get_artifact(report).model_copy(update={'repair_attempts': 1})
    with pytest.raises(ValueError, match='selected|report'):
        create_semantic_repair_authorization(factory, report=factory.store.put_artifact(forged), calls=calls,
            authorization='Synthetic unit approval')
    with pytest.raises(ValueError, match='three|distinct'):
        create_semantic_repair_authorization(factory, report=report, calls=(calls[0],)*3,
            authorization='Synthetic unit approval')
    auth = create_semantic_repair_authorization(factory, report=report, calls=calls,
        authorization='Synthetic unit approval')
    factory.authoring = factory.authoring.model_copy(update={'semantic_repair_authorization': auth})
    with pytest.raises(ValueError, match='authorization|exact'):
        factory.author(candidate, call=repaired(calls[0], 77))
    assert len(runner.calls) == 3
