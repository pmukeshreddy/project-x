from feature_rl.training.files import inspect_directory,publish_directory,verify_directory
from feature_rl.artifacts import ArtifactStore
from feature_rl.registry import Registry
from feature_rl.contracts import ActorRole
import pytest

def test_external_tensor_manifest_authenticates_all_files_without_tensor_cas(tmp_path):
    store=ArtifactStore(tmp_path/'objects',ActorRole.CONTROLLER);registry=Registry(tmp_path/'registry',store)
    tensors=tmp_path/'tensors';tensors.mkdir();(tensors/'model.pt').write_bytes(b'diagnostic tensor stand-in')
    (tensors/'rng.pt').write_bytes(b'diagnostic RNG stand-in')
    ref=publish_directory(store=store,registry=registry,path=tensors)
    assert verify_directory(store=store,ref=ref,path=tensors)==inspect_directory(tensors)
    (tensors/'rng.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='differs'):verify_directory(store=store,ref=ref,path=tensors)

def test_manifest_rejects_symlinks_and_detects_added_files(tmp_path):
    directory=tmp_path/'weights';directory.mkdir();(directory/'model').write_bytes(b'a')
    (directory/'alias').symlink_to(directory/'model')
    with pytest.raises(ValueError,match='symlinks'):inspect_directory(directory)
