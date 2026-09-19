"""Immutable controller receipts; a measured reward is not task admission."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.contracts import StrictModel, ArtifactRef, Disposition, UTCDateTime, Revision
from feature_rl.verifiers.models import Name, Names, unique


class AssertionResult(StrictModel):
    assertion_id: Name
    requirement_ids: Names
    passed: bool


class CaseResult(StrictModel):
    case_id: Name
    mandatory: bool
    status: Literal['completed','protocol_failure','candidate_failure','not_run','infrastructure_failure']
    passed: bool | None
    assertions: tuple[AssertionResult,...]
    evidence: ArtifactRef | None
    reason: Annotated[str,Field(min_length=1,max_length=1024)]

    @model_validator(mode='after')
    def coherent(self):
        unique([a.assertion_id for a in self.assertions],'assertion receipts')
        if self.status=='completed':
            if not self.assertions or self.evidence is None or self.passed is not all(a.passed for a in self.assertions):
                raise ValueError('incomplete comparison receipt')
        elif self.assertions or self.passed is not None:raise ValueError('uncompared case cannot claim assertions/pass')
        return self


class GradeReceipt(StrictModel):
    version: Literal['m4-grade-v1']
    task: ArtifactRef
    submission: ArtifactRef
    verifier: ArtifactRef | None
    case_seed: Annotated[int,Field(ge=0,le=2**63-1)]
    manifest: ArtifactRef | None
    source: ArtifactRef | None
    disposition: Disposition
    reward: Annotated[int,Field(ge=0,le=1)] | None
    reason: Annotated[str,Field(min_length=1,max_length=1024)]
    expected_case_ids: tuple[Name,...]
    cases: tuple[CaseResult,...]
    build_evidence: ArtifactRef | None
    runtime_evidence: tuple[ArtifactRef,...]
    cleanup_verified: bool
    implementation_revision: Revision
    recorded_at: UTCDateTime

    @model_validator(mode='after')
    def coherent(self):
        unique(self.expected_case_ids,'expected cases')
        if tuple(c.case_id for c in self.cases)!=self.expected_case_ids:raise ValueError('incomplete case ledger')
        measured=self.disposition in {Disposition.SUCCESS,Disposition.REJECTED}
        if measured!=(self.reward is not None):raise ValueError('unmeasured outcome requires null reward')
        if self.reward is not None and not self.cleanup_verified:raise ValueError('unverified cleanup cannot yield measured reward')
        if self.reward==1:
            if self.disposition!=Disposition.SUCCESS or not self.cases or self.manifest is None or self.source is None or self.build_evidence is None:
                raise ValueError('passing grade requires complete execution identity')
            if not any(c.mandatory for c in self.cases):raise ValueError('no required cases')
            if any(c.status!='completed' or (c.mandatory and not c.passed) for c in self.cases):
                raise ValueError('incomplete or failing required cases')
        if self.disposition==Disposition.SUCCESS and self.reward!=1:raise ValueError('successful grade must have reward one')
        if self.disposition==Disposition.REJECTED and self.reward!=0:raise ValueError('rejected candidate must have reward zero')
        return self
