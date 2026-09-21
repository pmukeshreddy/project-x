"""Read a frozen controller result without replaying qualification."""
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.registry import RegistryError
from .models import QualificationRejected


def assert_lifecycle_subject(built,later):
    try:
        same=canonical_json(built.model_dump(mode='json',exclude={'state','qualification'}))==canonical_json(
            later.model_dump(mode='json',exclude={'state','qualification'}))
    except (TypeError,ValueError):same=False
    if not same:raise QualificationRejected('invalid_evidence','later manifest changed the exact BUILT subject payload')


def _artifact(service,ref,kind):
    service.registry.assert_usable(ref)
    item=service.store.get_artifact(ref,max_envelope_bytes=4*1024*1024)
    if type(item) is not kind:raise QualificationRejected('invalid_evidence','wrong qualification artifact type')
    return item


def verify_accepted(service,task_ref,report_ref):
    """Private controller artifacts are authoritative; only hashes and identity are checked here."""
    try:
        task_ref=c.ArtifactRef.model_validate(task_ref)
        report_ref=c.ArtifactRef.model_validate(report_ref)
        if report_ref.visibility is not c.Visibility.PRIVATE:
            raise QualificationRejected('invalid_evidence','qualification report must remain controller-private')
        report=_artifact(service,report_ref,c.QualificationReport)
        if (report.provenance.producer,report.provenance.producer_version)!=(
                'feature_rl.qualification',service.revision):
            raise QualificationRejected('invalid_evidence','qualification origin/revision mismatch')
        if report.disposition!=c.Disposition.SUCCESS:
            raise QualificationRejected('provisional','qualification did not pass')
        if (report.policy_version!=service.policy.policy_id or len(report.provenance.inputs)!=3
                or report.provenance.inputs[:2]!=(report.task,service.policy_ref)
                or report.provenance.inputs[2].kind!='m5-reference-projection'):
            raise QualificationRejected('invalid_evidence','qualification task/policy/reference mismatch')
        built=_artifact(service,report.task,c.TaskBundle)
        if built.state!=c.TaskState.BUILT or built.qualification is not None:
            raise QualificationRejected('invalid_evidence','qualification requires its original BUILT task')
        later=built if task_ref==report.task else _artifact(service,task_ref,c.TaskBundle)
        assert_lifecycle_subject(built,later)
        if task_ref!=report.task and (later.state not in {c.TaskState.QUALIFIED,c.TaskState.CALIBRATED,c.TaskState.RELEASED}
                or later.qualification!=report_ref):
            raise QualificationRejected('invalid_evidence','task does not reference this qualification')
        return report
    except QualificationRejected:raise
    except (ArtifactError,RegistryError,ValueError) as exc:
        raise QualificationRejected('invalid_evidence','qualification artifact rejected: '+str(exc)[:1000]) from exc
