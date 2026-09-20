"""Explicit inert H implementation delta on B; no host source execution."""
import hashlib
from feature_rl.artifacts import ArtifactError
from feature_rl.contracts import CandidateRecord, SourcePair, TaskBundle, RequirementContract, TaskState, Visibility
from feature_rl.environments import SourceArchive, SourceRejected
from feature_rl.history.classification import classify_changed_files
from feature_rl.submission import SubmissionService
from feature_rl.submission.source import change_path, validate_rules
from .models import QualificationRejected, ProjectionPath, ReferenceProjection


def derive_reference(store, task_ref, policy):
    """Resolve exact M1 H and submit only the contract's justified feature files."""
    try:
        task=store.get_artifact(task_ref,max_envelope_bytes=1024*1024)
        if not isinstance(task,TaskBundle) or task.state!=TaskState.BUILT or task.qualification is not None:
            raise QualificationRejected('invalid_evidence','reference derivation requires complete unqualified BUILT root')
        pair=store.get_artifact(task.source_pair,max_envelope_bytes=1024*1024)
        contract=store.get_artifact(task.contract,max_envelope_bytes=1024*1024)
        if not isinstance(pair,SourcePair) or not isinstance(contract,RequirementContract):
            raise QualificationRejected('invalid_evidence','actual M0 source pair and contract required')
        if pair.baseline!=task.baseline or pair.reference!=task.reference_solution:
            raise QualificationRejected('invalid_evidence','task must retain exact SourcePair baseline and full H reference')
        candidate=store.get_artifact(pair.candidate,max_envelope_bytes=1024*1024)
        if not isinstance(candidate,CandidateRecord) or candidate.repository_family!=task.repository_family or candidate.request_lineage!=task.request_lineage or candidate.partition!=task.partition:
            raise QualificationRejected('invalid_evidence','source candidate/task lineage or partition mismatch')
        if pair.provenance_label!=candidate.provenance_label or contract.provenance_label!=pair.provenance_label:
            raise QualificationRejected('invalid_evidence','source and contract provenance-label mismatch')
        submissions=SubmissionService(store=store,policy=policy)
        before=submissions.source(pair.baseline);after=submissions.source(pair.reference)
        changed=sorted(name for name in set(before.files)|set(after.files) if before.files.get(name)!=after.files.get(name))
        classified={entry.path:entry for entry in pair.changed_files}
        if len(classified)!=len(pair.changed_files) or set(classified)!=set(changed):
            raise QualificationRejected('unrecoverable_history','changed-file classification must account for every actual B/H change exactly once')
        automatic={entry.path:entry.category for entry in classify_changed_files(tuple(changed)).changed_files}
        rules=validate_rules(contract.allowed_changes)
        policy.profile.validate_allowed_changes(rules)
        selected={item.path:item for item in contract.feature_files}
        if len(selected)!=len(contract.feature_files) or not set(selected)<=set(changed):
            raise QualificationRejected('invalid_evidence','every selected feature file must be a distinct actual B/H change')
        for name in selected:
            change_path(name,rules)
        projected=dict(before.files);paths=[];included=[]
        for name in changed:
            entry=classified[name];old=before.files.get(name);new=after.files.get(name)
            selection=selected.get(name)
            classification=f'history category: {entry.category}; path category: {automatic[name]}'
            if selection is None:
                action='excluded_unrelated'
                rationale=f'Not selected by the requirement contract; {classification}; {entry.rationale}'[:4096]
            else:
                action='included_implementation'
                rationale=(f'Required by {", ".join(selection.requirement_ids)}: '
                    f'{selection.rationale}; {classification}')[:4096]
            if action=='included_implementation':
                included.append(name)
                if new is None:projected.pop(name,None)
                else:projected[name]=new
            paths.append(ProjectionPath(path=name,action=action,rationale=rationale,
                before_sha256=hashlib.sha256(old.data).hexdigest() if old else None,
                after_sha256=hashlib.sha256(new.data).hexdigest() if new else None,
                before_executable=old.executable if old else None,after_executable=new.executable if new else None))
        if not included:raise QualificationRejected('unsupported_semantics','contract selects no changed feature files')
        delta=SourceArchive({name:after.files[name] for name in included if name in after.files}).to_tar()
        deletions=tuple(name for name in included if name not in after.files)
        submission=submissions.create(pair.baseline,delta,deletions,contract.allowed_changes)
        reconstructed=submissions.resolve(submission,pair.baseline,contract.allowed_changes)
        if reconstructed.files!=projected:raise QualificationRejected('invalid_evidence','M4 reconstruction disagrees with H projection')
        source=store.put_bytes(reconstructed.to_tar(),'source-archive',Visibility.PRIVATE)
        return ReferenceProjection(task=task_ref,source_pair=task.source_pair,baseline=pair.baseline,
            reference=pair.reference,projected_source=source,submission=submission,paths=tuple(paths))
    except SourceRejected as exc:
        raise QualificationRejected('unsupported_semantics','H implementation cannot follow candidate policy: '+str(exc)) from exc
    except ArtifactError as exc:
        raise QualificationRejected('invalid_evidence','source/reference artifact integrity: '+str(exc)) from exc
