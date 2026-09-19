import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest


def api():
    m=importlib.import_module('feature_rl.artifacts')
    assert hasattr(m,'ArtifactStore'), 'M0 immutable store is not implemented'
    c=importlib.import_module('feature_rl.contracts')
    return m,c


def test_bytes_roundtrip_metadata_identity_and_concurrent_duplicates(tmp_path):
    m,c=api(); store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    with ThreadPoolExecutor(max_workers=8) as pool:
        refs=list(pool.map(lambda _:store.put_bytes(b'actual bytes\x00','source',c.Visibility.PRIVATE),range(40)))
    assert len(set(r.sha256 for r in refs)) == 1
    assert store.get_bytes(refs[0]) == b'actual bytes\x00'
    public=store.put_bytes(b'actual bytes\x00','source',c.Visibility.PUBLIC)
    assert public.sha256 != refs[0].sha256
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_read_checks_digest_metadata_and_visibility(tmp_path):
    m,c=api(); store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    ref=store.put_bytes(b'secret','source',c.Visibility.PRIVATE)
    for role in (c.ActorRole.SOLVER,c.ActorRole.AUTHOR):
        with pytest.raises(m.AccessDenied):
            m.ArtifactStore(tmp_path,role).get_bytes(ref)
    forged=ref.model_copy(update={'visibility':c.Visibility.PUBLIC})
    with pytest.raises(m.ArtifactIntegrityError):
        store.get_bytes(forged)
    file=tmp_path/(ref.sha256+'.json')
    file.write_bytes(b'corrupt')
    with pytest.raises(m.ArtifactIntegrityError):
        store.get_bytes(ref)


def test_symlinks_and_interrupted_publication_fail_closed(tmp_path,monkeypatch):
    m,c=api(); store=m.ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)
    ref=store.put_bytes(b'data','source',c.Visibility.PUBLIC)
    path=tmp_path/'store'/(ref.sha256+'.json')
    target=tmp_path/'outside'; target.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(target)
    with pytest.raises(m.ArtifactIntegrityError): store.get_bytes(ref)
    import os
    def interrupt(*args,**kwargs): raise OSError('injected publication failure')
    monkeypatch.setattr(os,'link',interrupt)
    with pytest.raises(OSError): store.put_bytes(b'new data','source',c.Visibility.PUBLIC)
    assert target.read_bytes()
    assert len(list((tmp_path/'store').iterdir()))==1


def test_store_root_symlink_is_rejected(tmp_path):
    m,c=api(); (tmp_path/'target').mkdir(); (tmp_path/'link').symlink_to(tmp_path/'target')
    with pytest.raises(m.ArtifactIntegrityError): m.ArtifactStore(tmp_path/'link',c.ActorRole.CONTROLLER)


@pytest.mark.parametrize('payload',[
 b'{"encoding":"bytes","kind":"source","payload":"eA==","schema_version":1,"visibility":"public","visibility":"private"}',
 b'{"encoding":"bytes","kind":"source","payload":"eA==","schema_version":true,"visibility":"public"}',
 b'{"encoding":"bytes","kind":"source","payload":"eA==","schema_version":1,"visibility":"public","extra":1}',
 b'{"encoding":"bytes","kind":"source","payload":NaN,"schema_version":1,"visibility":"public"}',
])
def test_content_addressing_does_not_make_malformed_envelopes_valid(tmp_path,payload):
    import hashlib
    m,c=api(); store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    digest=hashlib.sha256(payload).hexdigest()
    (tmp_path/(digest+'.json')).write_bytes(payload)
    reference=c.ArtifactRef(sha256=digest,kind='source',schema_version=1,visibility=c.Visibility.PUBLIC,encoding='bytes')
    with pytest.raises(m.ArtifactIntegrityError): store.get_bytes(reference)


def test_atomic_crash_leaves_no_readable_partial_object_and_retry_works(tmp_path):
    import subprocess,sys,os
    m,c=api()
    code='''from pathlib import Path
import os,sys
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, Visibility
store=ArtifactStore(Path(sys.argv[1]),ActorRole.CONTROLLER)
os.link=lambda *a,**k: os._exit(19)
store.put_bytes(b'crash data','source',Visibility.PUBLIC)
'''
    child=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True,env=os.environ | {'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')})
    assert child.returncode==19,child.stderr
    assert list(tmp_path.glob('*.json'))==[]
    assert len(list(tmp_path.glob('.pending-*')))==1
    store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    reference=store.put_bytes(b'crash data','source',c.Visibility.PUBLIC)
    assert store.get_bytes(reference)==b'crash data'


def test_committed_corrupt_duplicate_is_not_overwritten(tmp_path):
    m,c=api();store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    reference=store.put_bytes(b'data','source',c.Visibility.PUBLIC)
    path=tmp_path/(reference.sha256+'.json');path.write_bytes(b'corrupt')
    with pytest.raises(m.ArtifactIntegrityError):store.put_bytes(b'data','source',c.Visibility.PUBLIC)
    assert path.read_bytes()==b'corrupt'


def test_role_cannot_write_unreadable_or_reviewer_objects(tmp_path):
    m,c=api()
    for role in (c.ActorRole.SOLVER,c.ActorRole.AUTHOR,c.ActorRole.TRAINER,c.ActorRole.EVALUATOR,c.ActorRole.REVIEWER):
        with pytest.raises(m.AccessDenied): m.ArtifactStore(tmp_path,role).put_bytes(b'secret','source',c.Visibility.PRIVATE)


def test_opaque_bytes_cannot_impersonate_typed_artifacts(tmp_path):
    m,c=api();store=m.ArtifactStore(tmp_path,c.ActorRole.CONTROLLER)
    with pytest.raises(m.ArtifactIntegrityError):store.put_bytes(b'{}','TaskBundle',c.Visibility.PUBLIC)
    reference=store.put_bytes(b'{}','raw-json',c.Visibility.PUBLIC)
    with pytest.raises(m.ArtifactIntegrityError):store.get_artifact(reference)


def test_root_replaced_with_symlink_after_initialization_is_denied(tmp_path):
    m,c=api();root=tmp_path/'root';store=m.ArtifactStore(root,c.ActorRole.CONTROLLER)
    root.rmdir();target=tmp_path/'target';target.mkdir();root.symlink_to(target)
    with pytest.raises(m.ArtifactIntegrityError):store.put_bytes(b'data','source',c.Visibility.PUBLIC)
    assert list(target.iterdir())==[]
