"""Thin concrete qualification and release orchestration."""
from feature_rl import contracts as c
from feature_rl.qualification import QualificationPolicy, QualificationService
from .packaging import checked, read_record, typed
from .lifecycle import AdmissionRejected, TaskLifecycle


def template(factory):
    if factory.qualification is None:
        raise AdmissionRejected('configure an actual QualificationService, runtime/package verifier before qualification/admission')
    return factory.qualification


def configured_service(factory,policy):
    current=template(factory)
    return QualificationService(store=factory.store,registry=factory.registry,grader=current.grader,
        builder=current.builder,revision=current.revision,policy=policy)


def qualify(factory,task_ref,policy):
    task_ref=checked(c.ArtifactRef,task_ref)
    task=typed(factory.store,task_ref,c.TaskBundle)
    if task.state!=c.TaskState.BUILT or task.qualification is not None:
        raise AdmissionRejected('qualification requires the complete original BUILT root')
    policy=template(factory).policy if policy is None else checked(QualificationPolicy,policy)
    return configured_service(factory,policy).qualify(task_ref)


def for_report(factory,report_ref):
    if report_ref.visibility!=c.Visibility.PRIVATE:
        raise AdmissionRejected('qualification report must retain private visibility')
    report=typed(factory.store,report_ref,c.QualificationReport)
    current=template(factory)
    if report.provenance.producer_version!=current.revision:
        raise AdmissionRejected('qualification report uses an unsupported M5 service revision')
    inputs=report.provenance.inputs
    if report.provenance.producer=='feature_rl.qualification' and len(inputs) in (2,3):
        if inputs[1].visibility!=c.Visibility.PRIVATE:
            raise AdmissionRejected('qualification policy must retain private visibility')
        policy=read_record(factory.store,inputs[1],QualificationPolicy,'m5-qualification-policy')
        return configured_service(factory,policy)
    raise AdmissionRejected('qualification report lacks actual M5 origin')


def release(factory,task_ref,report_ref):
    task_ref=checked(c.ArtifactRef,task_ref)
    task=typed(factory.store,task_ref,c.TaskBundle)
    report=task.qualification if report_ref is None else checked(c.ArtifactRef,report_ref)
    if report is None:raise AdmissionRejected('release requires an actual accepted qualification report')
    q=for_report(factory,report)
    lifecycle=TaskLifecycle(store=factory.store,registry=factory.registry,qualification=q,revision=factory.revision)
    if task.state==c.TaskState.BUILT:
        selected=lifecycle.qualify(task_ref,report)
        if selected.disposition!=c.Disposition.SUCCESS:return selected
        return lifecycle.release(selected.artifacts[0])
    if task.state!=c.TaskState.QUALIFIED or task.qualification!=report:
        raise AdmissionRejected('release requires BUILT plus accepted Q or its exact QUALIFIED predecessor')
    return lifecycle.release(task_ref)
