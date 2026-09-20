"""Source-only delta admission; hostile bytes are never executed."""
from m4_fixtures import runtime_policy
import io
import tarfile
import pytest
from feature_rl.contracts import AllowedChanges
from feature_rl.environments import SourceArchive, SourceFile, SandboxPolicy, SourceRejected


RULES=AllowedChanges(source_roots=('src',),forbidden_paths=('src/protected.py',),
    dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
BASE=SourceArchive({'pyproject.toml':SourceFile(b'baseline',False),
    'src/a.py':SourceFile(b'old',False),'src/protected.py':SourceFile(b'keep',False)})


def tar(name,data=b'x',kind=tarfile.REGTYPE,link=''):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as out:
        item=tarfile.TarInfo(name);item.type=kind;item.linkname=link;item.size=len(data) if kind==tarfile.REGTYPE else 0
        out.addfile(item,io.BytesIO(data) if item.size else None)
    return stream.getvalue()


def test_delta_rebuild_keeps_baseline_and_supports_deletion():
    from feature_rl.submission import apply_delta
    result=apply_delta(BASE,tar('src/new.py',b'raise RuntimeError("never on host")'),('src/a.py',),RULES,runtime_policy())
    assert set(result.files)=={'pyproject.toml','src/protected.py','src/new.py'}
    assert result.files['pyproject.toml']==BASE.files['pyproject.toml']


@pytest.mark.parametrize('payload',[
    tar('../escape'),tar('/outside'),tar('src/../escape'),tar('src/a.py/'),
    tar('src/a.py',kind=tarfile.SYMTYPE,link='/outside'),
    tar('src/a.py',kind=tarfile.LNKTYPE,link='src/protected.py'),
    tar('src/a.py',kind=tarfile.CHRTYPE),tar('pyproject.toml'),tar('tests/result.json'),
    tar('src/protected.py'),tar('src/__pycache__/a.pyc'),tar('src/a.whl'),
],ids=['traversal','absolute','dotdot','trailing','symlink','hardlink','device','build','verdict','forbidden','cache','wheel'])
def test_submission_rejects_archive_escape_links_build_and_verdict_paths(payload):
    from feature_rl.submission import apply_delta
    with pytest.raises(SourceRejected):apply_delta(BASE,payload,(),RULES,runtime_policy())


def test_submission_rejects_duplicate_ambiguous_or_missing_deletions():
    from feature_rl.submission import apply_delta
    empty=SourceArchive({}).to_tar()
    for deletions in [('src/a.py','src/a.py'),('src/no.py',),('src/a.py/',),('pyproject.toml',)]:
        with pytest.raises(SourceRejected):apply_delta(BASE,empty,deletions,RULES,runtime_policy())
    with pytest.raises(SourceRejected):apply_delta(BASE,tar('src/a.py'),('src/a.py',),RULES,runtime_policy())


def test_saved_source_submission_keeps_exact_last_confirmed_content(tmp_path):
    from feature_rl.submission import SubmissionService
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole, Visibility
    store=ArtifactStore(tmp_path/'store',ActorRole.CONTROLLER)
    base=store.put_bytes(BASE.to_tar(),'source-archive',Visibility.AUTHORING)
    saved=store.put_bytes(SourceArchive(BASE.files|{'src/a.py':SourceFile(b'confirmed edit',False)}).to_tar(),'source-archive',Visibility.PRIVATE)
    service=SubmissionService(store=store,policy=runtime_policy())
    submission=service.from_saved(base,saved,RULES)
    rebuilt=service.resolve(submission,base,RULES)
    assert rebuilt.files['src/a.py'].data==b'confirmed edit'
    assert rebuilt.files['pyproject.toml'].data==b'baseline'
    assert submission==service.from_saved(base,saved,RULES)
