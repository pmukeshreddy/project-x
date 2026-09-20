"""Controller boundaries; no historical or downloaded code runs in these tests."""
from m4_fixtures import runtime_policy
import io
import tarfile
import pytest


def archive(entries):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as tar:
        for name,data,kind in entries:
            item=tarfile.TarInfo(name)
            if kind=='file':
                item.size=len(data);tar.addfile(item,io.BytesIO(data))
            else:
                item.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE
                item.linkname=data.decode();tar.addfile(item)
    return stream.getvalue()


def test_archive_canonicalizes_data_without_executing_it():
    from feature_rl.environments import SourceArchive, SandboxPolicy
    result=SourceArchive.read(archive([('src/a.py',b'raise RuntimeError("must never import")','file')]),runtime_policy())
    assert result.files['src/a.py'].data==b'raise RuntimeError("must never import")'
    assert len(result.tree_sha256)==64
    assert SourceArchive.read(result.to_tar(),runtime_policy()).tree_sha256==result.tree_sha256


@pytest.mark.parametrize('name,kind,data',[
    ('../outside','file',b'x'),('/etc/passwd','file',b'x'),('src/../../x','file',b'x'),
    ('src\\escape','file',b'x'),('.git/config','file',b'x'),('src/__pycache__/a.pyc','file',b'x'),
    ('src/link','symlink',b'/etc/passwd'),('src/link','hardlink',b'src/a.py'),
])
def test_archive_rejects_escape_links_and_history(name,kind,data):
    from feature_rl.environments import SourceArchive,SandboxPolicy,SourceRejected
    with pytest.raises(SourceRejected):SourceArchive.read(archive([(name,data,kind)]),runtime_policy())


def test_archive_rejects_duplicate_paths_and_expanded_limit():
    from feature_rl.environments import SourceArchive,SandboxPolicy,SourceRejected
    with pytest.raises(SourceRejected):SourceArchive.read(archive([('a',b'a','file'),('a',b'b','file')]),runtime_policy())
    with pytest.raises(SourceRejected):SourceArchive.read(archive([('a',b'12345','file')]),runtime_policy(max_source_bytes=4))


def test_saved_source_cannot_modify_build_manifest_outside_allowed_roots():
    from feature_rl.environments import SourceArchive,SandboxPolicy,SourceRejected
    p=runtime_policy();b=SourceArchive.read(archive([('pyproject.toml',b'old','file'),('src/a.py',b'a','file')]),p)
    good=SourceArchive.read(archive([('pyproject.toml',b'old','file'),('src/a.py',b'b','file')]),p)
    good.validate_changes(b,('src',),())
    bad=SourceArchive.read(archive([('pyproject.toml',b'new','file'),('src/a.py',b'a','file')]),p)
    with pytest.raises(SourceRejected):bad.validate_changes(b,('src',),())


def test_policy_rejects_nonfinite_and_unbounded_inputs():
    from feature_rl.environments import SandboxPolicy
    for kw in [{'lifecycle_seconds':float('inf')},{'max_source_bytes':0},{'cleanup_seconds':0},{'cpus':1000.0}]:
        with pytest.raises(ValueError):runtime_policy(**kw)


def test_admitted_long_paths_survive_canonical_archive_roundtrip():
    from feature_rl.environments import SourceArchive,SandboxPolicy
    name='src/'+'x'*180+'.py'
    result=SourceArchive.read(archive([(name,b'valid','file')]),runtime_policy())
    assert SourceArchive.read(result.to_tar(),runtime_policy()).files[name].data==b'valid'


def test_boundary_gate_rejects_printed_unexpected_success():
    from feature_rl.environments.probes import check_boundary
    from feature_rl.environments import PolicyRejected,SandboxPolicy
    with pytest.raises(PolicyRejected):check_boundary({'uid':0},runtime_policy())


def test_runtime_refuses_unqualified_engine_before_source_execution(tmp_path):
    from feature_rl.environments import EnvironmentRuntime,PolicyRejected
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole
    class Unqualified:
        qualified=False
    with pytest.raises(PolicyRejected):EnvironmentRuntime(store=ArtifactStore(tmp_path/'store',ActorRole.CONTROLLER),engine=Unqualified(),revision='a'*40)


def test_evidence_publication_failure_retains_bounded_receipt_for_retry(tmp_path,monkeypatch):
    from feature_rl.environments import EnvironmentRuntime,EvidencePublicationFailed
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole
    runtime=object.__new__(EnvironmentRuntime);runtime.store=ArtifactStore(tmp_path/'store',ActorRole.CONTROLLER)
    real=runtime.store.put_bytes
    with monkeypatch.context() as patch:
        patch.setattr(runtime.store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(OSError('test storage unavailable')))
        with pytest.raises(EvidencePublicationFailed) as caught:runtime.publish({'cleanup_verified':True,'extra':{'saved_source':{'version':2}}},'environment-execution')
    assert caught.value.failure_category=='infrastructure' and caught.value.cleanup_verified
    ref=runtime.retry_publication(caught.value)
    assert b'"version":2' in runtime.store.get_bytes(ref)


def test_forged_in_memory_archive_cannot_serialize_unsafe_paths():
    from feature_rl.environments import SourceArchive,SourceFile,SourceRejected
    with pytest.raises(SourceRejected):SourceArchive({'../escape':SourceFile(b'x',False)}).to_tar()
    with pytest.raises(SourceRejected):SourceArchive({'src/a.py':SourceFile('not bytes',False)}).to_tar()
