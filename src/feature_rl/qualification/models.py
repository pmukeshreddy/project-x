"""Controller-local records for the bounded qualification check."""
from typing import Literal
from feature_rl.contracts import StrictModel, Disposition
from feature_rl.verifiers.models import Name

ReasonCode = Literal['accepted','provisional','ambiguous_requirement','unsupported_semantics',
    'environment_failure','invalid_evidence']

class QualificationRejected(ValueError):
    def __init__(self, code: ReasonCode, detail: str):
        self.code=code;self.detail=detail
        super().__init__(code+': '+detail)

class QualificationPolicy(StrictModel):
    version: Literal['m5-pilot-policy-v1']='m5-pilot-policy-v1'
    policy_id: Name='pilot-v1'


def disposition_for(issues):
    if not issues:return Disposition.SUCCESS
    codes={issue.split(':',1)[0] for issue in issues}
    if 'ambiguous_requirement' in codes:return Disposition.REJECTED
    if 'invalid_evidence' in codes:return Disposition.INVALID
    if 'environment_failure' in codes:return Disposition.INFRASTRUCTURE
    if 'unsupported_semantics' in codes:return Disposition.UNSUPPORTED
    return Disposition.PROVISIONAL
