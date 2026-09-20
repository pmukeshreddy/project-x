"""Explicit immutable quarantine scope for historical audit jobs only."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, StrictModel, Visibility
from .models import RegistryConflict

POLICY='registry-historical-audit-policy'
SUBJECTS='registry-historical-audit-subjects'
PROTECTED='registry-historical-audit-protected'
RESERVED={POLICY,SUBJECTS,PROTECTED}


class AuditReferences(StrictModel):
    version: Literal['registry-audit-references-v1']='registry-audit-references-v1'
    references: Annotated[tuple[ArtifactRef,...],Field(min_length=1,max_length=256)]

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.references))!=len(self.references):raise ValueError('duplicate audit scope reference')
        if any(ref.kind in RESERVED for ref in self.references):raise ValueError('nested historical scopes are forbidden')
        return self


class AuditPolicy(StrictModel):
    version: Literal['registry-historical-audit-v1']='registry-historical-audit-v1'
    configuration: ArtifactRef
    subjects: ArtifactRef
    protected: ArtifactRef

    @model_validator(mode='after')
    def exact_refs(self):
        if self.configuration.kind in RESERVED:raise ValueError('wrapped original audit configuration required')
        for ref,kind in ((self.subjects,SUBJECTS),(self.protected,PROTECTED)):
            if (ref.kind,ref.schema_version,ref.visibility,ref.encoding)!=(kind,1,Visibility.PRIVATE,'bytes'):
                raise ValueError('exact private audit reference groups required')
        return self


def intrinsic(ref,payload):
    """Validate these reserved controller records; all other bytes stay opaque."""
    if ref.kind not in RESERVED:return ()
    if (ref.schema_version,ref.visibility,ref.encoding)!=(1,Visibility.PRIVATE,'bytes'):
        raise RegistryConflict('historical audit configuration metadata rejected')
    try:
        model=AuditPolicy if ref.kind==POLICY else AuditReferences
        value=model.model_validate_json(payload)
        if canonical_json(value.model_dump(mode='json'))!=payload:raise ValueError('noncanonical audit scope')
    except ValueError as exc:
        raise RegistryConflict('invalid reserved historical audit record') from exc
    return (value.configuration,value.subjects,value.protected) if isinstance(value,AuditPolicy) else value.references


def ignored_roots(state,spec):
    if spec.operation!='audit' or spec.configuration.kind!=POLICY:return frozenset()
    record=state.artifacts.get(spec.configuration.sha256)
    if record is None or record.ref!=spec.configuration:raise RegistryConflict('audit scope is not registered')
    subjects=[r for r in record.dependencies if r.kind==SUBJECTS]
    protected=[r for r in record.dependencies if r.kind==PROTECTED]
    configuration=[r for r in record.dependencies if r.kind not in RESERVED]
    if len(record.dependencies)!=3 or len(subjects)!=1 or len(protected)!=1 or len(configuration)!=1:
        raise RegistryConflict('historical audit scope dependency shape differs')
    guards={spec.configuration.sha256,*(r.sha256 for r in record.dependencies),
        *(r.sha256 for r in state.artifacts[protected[0].sha256].dependencies)}
    seen=set();pending=list(state.artifacts[subjects[0].sha256].dependencies)
    while pending:
        ref=pending.pop()
        if ref.sha256 in seen:continue
        seen.add(ref.sha256)
        if ref.kind in RESERVED:raise RegistryConflict('nested scope in historical subject closure')
        pending.extend(state.artifacts[ref.sha256].dependencies)
    # Protected refs are explicit current instructions/envelopes/payloads/
    # signatures. Their historical subjects may be dependencies, so protecting
    # their entire transitive closure would recreate the original audit deadlock.
    return frozenset(seen-guards)
