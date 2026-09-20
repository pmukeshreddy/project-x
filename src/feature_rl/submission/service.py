"""Durable source delta publication and exact baseline reconstruction."""
import json
from feature_rl.artifacts import canonical_json, ArtifactIntegrityError, ArtifactSizeLimitError
from feature_rl.contracts import ArtifactRef, Visibility
from feature_rl.environments import SourceArchive, SourceRejected
from .source import Submission, apply_delta


class SubmissionService:
    def __init__(self,*,store,policy):
        self.store=store
        self.policy=policy

    def source(self,ref):
        ref=ArtifactRef.model_validate(ref)
        if ref.kind!='source-archive':raise SourceRejected('source archive required')
        cap=self.policy.max_archive_bytes
        return SourceArchive.read(self.store.get_bytes(ref,max_envelope_bytes=4*((cap+2)//3)+4096,max_payload_bytes=cap),self.policy)

    def create(self,baseline,archive,deletions,allowed_changes):
        apply_delta(self.source(baseline),archive,deletions,allowed_changes,self.policy)
        changes=self.store.put_bytes(archive,'m4-source-delta',Visibility.PRIVATE)
        value=Submission(version='m4-submission-v1',baseline=baseline,changes=changes,deletions=deletions)
        return self.store.put_bytes(canonical_json(value.model_dump(mode='json')),'m4-submission',Visibility.PRIVATE)

    def from_saved(self,baseline,saved_source,allowed_changes):
        old=self.source(baseline);new=self.source(saved_source).without_pytest_cache(old)
        changes={k:v for k,v in new.files.items() if old.files.get(k)!=v}
        deletions=tuple(sorted(old.files.keys()-new.files.keys()))
        return self.create(baseline,SourceArchive(changes).to_tar(),deletions,allowed_changes)

    def resolve(self,submission,baseline,allowed_changes):
        # Import locally to avoid a package-init cycle; parser is inert.
        from feature_rl.verifiers.language import decode_json
        # B is trusted task material, not part of this candidate's delta. Resolve
        # it once before candidate parsing and keep its failures outside the
        # SourceRejected/size-limit category used for measured candidate zeros.
        try:trusted_baseline=self.source(baseline)
        except (SourceRejected,ArtifactSizeLimitError) as exc:
            raise ArtifactIntegrityError('invalid trusted baseline: '+str(exc)) from exc
        ref=ArtifactRef.model_validate(submission)
        if ref.kind!='m4-submission' or ref.encoding!='bytes':raise SourceRejected('M4 submission bytes required')
        data=self.store.get_bytes(ref,max_envelope_bytes=128*1024,max_payload_bytes=65536)
        try:value=Submission.model_validate_json(canonical_json(decode_json(data,65536)))
        except ValueError as exc:raise SourceRejected('invalid source submission manifest') from exc
        if value.baseline!=baseline:raise SourceRejected('submission baseline mismatch')
        if value.changes.kind!='m4-source-delta' or value.changes.encoding!='bytes':raise SourceRejected('source delta bytes required')
        cap=self.policy.max_archive_bytes
        data=self.store.get_bytes(value.changes,max_envelope_bytes=4*((cap+2)//3)+4096,max_payload_bytes=cap)
        return apply_delta(trusted_baseline,data,value.deletions,allowed_changes,self.policy)
