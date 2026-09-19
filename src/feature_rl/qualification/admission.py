"""External-review admission boundary, implemented separately from worker execution."""
from .models import QualificationRejected


def accept(service,review_request,attestation):
    raise QualificationRejected('unverified_human_review','No externally authenticated HUMAN evidence has been verified')


def verify_accepted(service,task_ref,report_ref):
    raise QualificationRejected('unverified_human_review','Accepted report requires authentic selected qualification and external human evidence')
