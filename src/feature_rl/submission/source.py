"""Positive source delta allowlist over B; archives stay inert on the controller."""
import io
import tarfile
from typing import Annotated, Literal
from pydantic import Field
from feature_rl.contracts import AllowedChanges, ArtifactRef, StrictModel
from feature_rl.environments import SourceArchive, SourceRejected
from feature_rl.environments.archive import safe_path


class Submission(StrictModel):
    version: Literal['m4-submission-v1']
    baseline: ArtifactRef
    changes: ArtifactRef
    deletions: Annotated[tuple[str,...],Field(max_length=2000)]


def change_path(path,rules):
    if safe_path(path)!=path:raise SourceRejected('ambiguous source path')
    if '.pytest_cache' in path.split('/'):raise SourceRejected('unauthorized pytest cache change')
    if not any(path==r or path.startswith(r+'/') for r in rules.source_roots):raise SourceRejected('outside source roots')
    if any(path==r or path.startswith(r+'/') for r in rules.forbidden_paths):raise SourceRejected('forbidden path')


def validate_rules(rules):
    rules=AllowedChanges.model_validate(rules)
    if (rules.additional_artifact_types
            or (rules.dependencies=='forbidden' and rules.dependency_artifacts)
            or len(set(rules.dependency_artifacts))!=len(rules.dependency_artifacts)
            or any(ref.kind!='dependency-wheel' or ref.encoding!='bytes' for ref in rules.dependency_artifacts)):
        raise SourceRejected('unsupported dependency/artifact policy')
    if len(set(rules.source_roots))!=len(rules.source_roots):raise SourceRejected('duplicate roots')
    for path in (*rules.source_roots,*rules.forbidden_paths):
        if safe_path(path)!=path:raise SourceRejected('ambiguous rule path')
    return rules


def apply_delta(baseline,archive,deletions,rules,policy):
    rules=validate_rules(rules)
    policy.profile.validate_allowed_changes(rules)
    if type(deletions) is not tuple or len(deletions)>policy.max_files:raise SourceRejected('deletion count/type')
    changes=SourceArchive.read(archive,policy).without_pytest_cache(baseline)
    # M3 accepts benign baseline directory entries. A submission is a strict file
    # delta: directory aliases or trailing-slash regular files are ambiguous.
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as stream:
        for item in stream:
            if not item.isfile() or safe_path(item.name)!=item.name:raise SourceRejected('noncanonical delta member')
    if len(set(deletions))!=len(deletions):raise SourceRejected('duplicate deletion')
    result=dict(baseline.files)
    for name in deletions:
        change_path(name,rules)
        if name not in baseline.files or name in changes.files:raise SourceRejected('missing or contradictory deletion')
        del result[name]
    for name,entry in changes.files.items():
        change_path(name,rules)
        result[name]=entry
    merged=SourceArchive.read(SourceArchive(result).to_tar(),policy)
    merged.validate_changes(baseline,rules.source_roots,rules.forbidden_paths)
    return merged
