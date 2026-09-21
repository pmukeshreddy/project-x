"""Authenticate automated qualification through its selected execution ledger."""
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.environments import SourceArchive
from feature_rl.registry import JobSpec, RegistryError
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from .controls import assess_outcome, validate_control_plan, control_origins, diagnose_control
from .evidence import collapse_costs, unknown_cost, validate_grade, validate_reset, assert_reference_determinism
from .models import QualificationRejected, ReferenceProjection, RunBinding, QualificationSummary
from .projection import derive_reference
from .schedule import control_run_name, control_seeds


def _compare_payload(left,right,excluded,detail):
    try:
        same=canonical_json(left.model_dump(mode='json',exclude=excluded))==canonical_json(right.model_dump(mode='json',exclude=excluded))
    except (TypeError,ValueError):same=False
    if not same:raise QualificationRejected('invalid_evidence',detail)


def assert_lifecycle_subject(built,later):
    """M6 separately authenticates legal transition records for any later state."""
    _compare_payload(built,later,{'state','qualification'},'later manifest changed the exact BUILT subject payload')


def _artifact(service,ref,kind):
    service.registry.assert_usable(ref)
    item=service.store.get_artifact(ref,max_envelope_bytes=4*1024*1024)
    if type(item) is not kind:raise QualificationRejected('invalid_evidence','wrong qualification artifact type')
    return item


def _spec(service,inputs,invocation,operation='qualify',configuration=None):
    return JobSpec(operation=operation,inputs=inputs,configuration=configuration or service.configuration,
        implementation=service.revision,invocation=invocation,attempt_limit=3)


def _selected(service,job_id,spec,output):
    job=service.registry.job(job_id)
    if job.spec!=spec or job.state!='completed' or job.result is None or output not in job.result.artifacts:
        raise QualificationRejected('invalid_evidence','evidence is not selected by its exact completed controller operation')
    return job.result


def expected_runs(service,checked,projection,*,generated=()):
    missing,targets=validate_control_plan(checked,service.policy,generated=generated)
    baseline=service.grader.submissions.create(checked.task.baseline,SourceArchive({}).to_tar(),(),checked.contract.allowed_changes)
    runs=[('baseline_absence',baseline,service.policy.fresh_seeds[0],'semantic_negative',targets,False)]
    runs.extend(('fresh_'+str(i),projection.submission,s,'positive',(),False) for i,s in enumerate(service.policy.fresh_seeds))
    by_id={d.control_id:d for d in (*service.policy.controls,*generated)}
    automatic={d.control_id for d in generated}
    for control in checked.verifier.controls:
        d=by_id.get(control.control_id)
        if d is not None and d.validity not in {'equivalent','unresolved'}:
            for index,seed in enumerate(control_seeds(service.policy)):
                mode,targets=(None,control.requirement_ids) if index==0 and control.control_id in automatic else (d.mode,d.targets)
                runs.append((control_run_name(control.control_id,index),control.patch,seed,mode,targets,False))
    runs.extend(('reset_'+str(i),projection.submission,s,'positive',(),True) for i,s in enumerate(service.policy.reset_seeds))
    return tuple(runs),missing


def _gate(service,gate,name,task,binding,targets,outcome):
    if gate is None or (gate.name,gate.subject,gate.disposition,gate.passed,gate.requirement_ids,gate.reason)!=(name,task,c.Disposition.SUCCESS,True,targets,outcome.detail) or not outcome.passed:
        raise QualificationRejected('invalid_evidence','frozen gate does not match actual scheduled outcome: '+name)
    if len(gate.evidence)!=1:
        raise QualificationRejected('invalid_evidence','unexpected gate evidence replacement: '+name)
    ev=gate.evidence[0]
    command_name='baseline_absence' if name=='baseline_health' else name
    if (ev.producer,ev.revision,ev.exit_status,ev.scope,ev.artifacts,ev.command)!=('feature_rl.qualification',service.revision,0,'real_integration',(binding,),('QualificationService.execute',command_name,task.sha256)):
        raise QualificationRejected('invalid_evidence','gate receipt provenance differs from qualification execution: '+name)


def _qualification_context(service,report_ref,result,qualification_job):
    """Recheck the complete automated execution ledger."""
    service.registry.assert_usable(service.configuration)
    report=_artifact(service,report_ref,c.QualificationReport)
    disposition=c.Disposition.SUCCESS
    if result.operation!='qualify' or result.disposition!=disposition or not result.artifacts or result.artifacts[0]!=report_ref:
        raise QualificationRejected('invalid_evidence','qualification report is not the selected execution package')
    if report.disposition!=disposition or report.rejection_reasons or report.policy_version!=service.policy.policy_id:
        raise QualificationRejected('invalid_evidence','qualification package has incomplete or substituted qualification gates')
    if report.provenance.producer!='feature_rl.qualification' or report.provenance.producer_version!=service.revision or len(report.provenance.inputs)!=3:
        raise QualificationRejected('invalid_evidence','qualification package origin/revision mismatch')
    task_ref,policy_ref,summary_ref=report.provenance.inputs
    if (task_ref,policy_ref)!=(report.task,service.policy_ref) or summary_ref not in result.artifacts:
        raise QualificationRejected('invalid_evidence','qualification package summary/policy/task mismatch')
    service.registry.assert_usable(summary_ref)
    summary=read_local(service.store,summary_ref,QualificationSummary,'m5-qualification-summary',4*1024*1024)
    if (report.semantic_repair_authorization!=service.policy.semantic_repair_authorization
            or summary.semantic_repair_authorization!=service.policy.semantic_repair_authorization):
        raise QualificationRejected('invalid_evidence','qualification semantic authorization differs from its exact policy')
    if (summary.task,summary.policy,summary.qualification_job)!=(report.task,service.policy_ref,qualification_job) or summary.issues or summary.projection is None:
        raise QualificationRejected('invalid_evidence','qualification summary does not bind the complete frozen package')
    if summary.wall_seconds is None or summary.wall_seconds>service.policy.max_wall_seconds:
        raise QualificationRejected('budget_exhausted','qualification package lacks a completed bounded qualification wall measurement')
    checked=load_verifier(service.store,report.task)
    service.grader.select_task(checked)
    if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
        raise QualificationRejected('invalid_evidence','Q.task must be exact unqualified BUILT T0')
    if any(a.disposition=='unresolved' for a in checked.contract.ambiguities):
        raise QualificationRejected('ambiguous_requirement','unresolved frozen contract ambiguity')
    if service.builder is None:raise QualificationRejected('provisional','actual M6 frozen package validator is required')
    service.builder.solver_package(report.task)
    service.registry.assert_usable(summary.projection)
    projection=read_local(service.store,summary.projection,ReferenceProjection,'m5-reference-projection',1024*1024)
    if projection!=derive_reference(service.store,report.task,service.grader.submissions.policy):
        raise QualificationRejected('invalid_evidence','qualified H projection differs from exact B/H bytes and policy')
    count,missing_history=service._history(checked)
    if missing_history or count is None or report.repair_attempts!=count or summary.repair_count!=count:
        raise QualificationRejected('invalid_evidence','complete selected global repair history is required for acceptance')
    runs,missing=expected_runs(service,checked,projection,generated=summary.control_diagnoses)
    if missing:raise QualificationRejected('provisional','; '.join(missing))
    if len(runs)>service.policy.max_grade_calls or len(summary.bindings)!=len(runs) or len(set(summary.bindings))!=len(runs):
        raise QualificationRejected('invalid_evidence','missing, excessive or replayed qualification run bindings')
    gates=[report.baseline_absence,*report.fresh_runs,*report.controls,*report.interrupted_reset_runs]
    if len(gates)!=len(runs) or report.reference_run!=(report.fresh_runs[0] if report.fresh_runs else None):
        raise QualificationRejected('invalid_evidence','incomplete frozen reference/control/reset gate ledger')
    seen=set();costs=[];signatures={}
    generated={d.control_id:d for d in summary.control_diagnoses}
    origins=control_origins(service,checked) if generated else {}
    controls={control.control_id:control for control in checked.verifier.controls}
    automatic_names={control_run_name(control_id,0):control_id for control_id in generated}
    for scheduled,binding_ref,gate in zip(runs,summary.bindings,gates):
        name,submission,seed,mode,targets,reset=scheduled
        service.registry.assert_usable(binding_ref)
        binding=read_local(service.store,binding_ref,RunBinding,'m5-run-binding')
        if (binding.name,binding.task,binding.projection,binding.submission,binding.seed,binding.mode,binding.targets)!=(name,report.task,summary.projection,submission,seed,mode,targets) or (binding.reset is not None)!=reset:
            raise QualificationRejected('invalid_evidence','scheduled run identity or source/projection mismatch')
        job=service.registry.job(binding.grade_job)
        source_refs=service._source_dependencies(checked,submission)
        config={'version':'m5-run-configuration-v2','configuration':service.configuration.model_dump(mode='json'),
            'projection':summary.projection.model_dump(mode='json'),'submission':submission.model_dump(mode='json'),
            'source_dependencies':[ref.model_dump(mode='json') for ref in source_refs],
            'seed':seed,'name':name,'mode':mode,'targets':list(targets),'reset':reset}
        from feature_rl.verifiers.loader import read_bytes
        if read_bytes(service.store,job.spec.configuration,1024*1024,'m5-run-configuration',True)!=canonical_json(config):
            raise QualificationRejected('invalid_evidence','run configuration differs from the frozen schedule')
        actual=_selected(service,binding.grade_job,_spec(service,(report.task,),
            'm5-run:'+qualification_job+':'+name,'grade',job.spec.configuration),binding.grade)
        if actual.artifacts[0]!=binding.grade:
            raise QualificationRejected('invalid_evidence','run substituted its selected grade receipt')
        receipt,ids=validate_grade(service.store,checked,submission,seed,actual,service.grader,seen=seen)
        if binding.operation_ids!=ids:
            raise QualificationRejected('invalid_evidence','run operation identities differ from actual runtime')
        if name.startswith(('fresh_','reset_')):
            assert_reference_determinism(service.store,checked,receipt,signatures)
        if reset:
            if binding.reset not in actual.artifacts:
                raise QualificationRejected('invalid_evidence','reset was not produced by this selected grade operation')
            validate_reset(service.store,checked,summary.projection,binding.reset,service.grader,seen=seen)
        if mode is None:
            control_id=automatic_names[name]
            diagnosis,outcome=diagnose_control(service,checked,controls[control_id],receipt,binding_ref,origins)
            if diagnosis!=generated[control_id]:
                raise QualificationRejected('invalid_evidence','automatic control diagnosis differs from retained execution/authorship')
        else:
            outcome=assess_outcome(checked,receipt,mode,targets,store=service.store)
        _gate(service,gate,name,report.task,binding_ref,targets,outcome)
        if name=='baseline_absence':
            health=assess_outcome(checked,receipt,'baseline_health',())
            _gate(service,report.baseline_health,'baseline_health',report.task,binding_ref,
                tuple(r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory),health)
        costs.extend(actual.costs)
    if report.costs!=collapse_costs((*costs,unknown_cost())):
        raise QualificationRejected('invalid_evidence','qualification package substituted original execution costs')
    if result.costs!=report.costs or result.evidence!=report.provenance.evidence:
        raise QualificationRejected('invalid_evidence','qualification result changed its report evidence or costs')
    return report,checked


def validate_pending_admission(service,result,claim=None):
    """Publication-only retries recheck the original qualification evidence."""
    report=_artifact(service,result.artifacts[0],c.QualificationReport)
    if claim is None:
        raise QualificationRejected('invalid_evidence','qualification publication requires its selected claim')
    job=service.registry.job(claim.job_id)
    if job.spec!=_spec(service,(report.task,),'m5-qualify') or not any(
        item.claim==claim for item in service.registry.attempts(claim.job_id)):
        raise QualificationRejected('invalid_evidence','qualification publication changed its original job or claim')
    _qualification_context(service,result.artifacts[0],result,claim.job_id)


def verify_accepted(service,task_ref,report_ref):
    try:
        task_ref=c.ArtifactRef.model_validate(task_ref)
        report_ref=c.ArtifactRef.model_validate(report_ref)
        report=_artifact(service,report_ref,c.QualificationReport)
        if report.provenance.producer!='feature_rl.qualification':
            raise QualificationRejected('invalid_evidence','qualification lacks automated execution origin')
        if report.disposition!=c.Disposition.SUCCESS:
            raise QualificationRejected('provisional','qualification has unresolved or failed automated gates')
        if len(report.provenance.inputs)!=3:
            raise QualificationRejected('invalid_evidence','qualification lacks its original execution summary')
        summary=read_local(service.store,report.provenance.inputs[-1],QualificationSummary,'m5-qualification-summary',4*1024*1024)
        result=_selected(service,summary.qualification_job,_spec(service,(report.task,),'m5-qualify'),report_ref)
        accepted,checked=_qualification_context(service,report_ref,result,summary.qualification_job)
        later=_artifact(service,task_ref,c.TaskBundle)
        assert_lifecycle_subject(checked.task,later)
        if task_ref!=accepted.task and (later.state not in {c.TaskState.QUALIFIED,c.TaskState.CALIBRATED,c.TaskState.RELEASED} or later.qualification!=report_ref):
            raise QualificationRejected('invalid_evidence','later task does not reference this exact qualification')
        return accepted
    except QualificationRejected:raise
    except (ArtifactError,RegistryError,ValueError) as exc:
        raise QualificationRejected('invalid_evidence','accepted qualification dependency/origin rejected: '+str(exc)[:1000]) from exc
