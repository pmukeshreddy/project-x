import hashlib

import pytest

from feature_rl.artifacts import ArtifactStore
from feature_rl import contracts as c
from feature_rl.environments import SandboxPolicy, WheelPin, PolicyRejected
from feature_rl.environments.candidates import CatalogWheel, _inspect_wheel
from m4_fixtures import diagnostic_wheel


@pytest.mark.parametrize('nested', [True, False])
def test_vendored_metadata_is_package_data_but_second_distribution_is_rejected(tmp_path, nested):
    path = ('package/vendor/' if nested else '') + 'vendor-2.0.dist-info/METADATA'
    data = diagnostic_wheel('package', '1.0', extra_files={path: b'Name: vendor\nVersion: 2.0\n'})
    store = ArtifactStore(tmp_path.resolve()/'store', c.ActorRole.CONTROLLER)
    ref = store.put_bytes(data, 'dependency-wheel', c.Visibility.AUTHORING)
    pin = WheelPin(name='package', version='1.0', filename='package-1.0-py3-none-any.whl',
                   sha256=hashlib.sha256(data).hexdigest())
    item = CatalogWheel(pin=pin, artifact=ref)
    policy = SandboxPolicy(platform='linux/arm64')
    if nested:
        assert _inspect_wheel(store, item, policy).name == 'package'
    else:
        with pytest.raises(PolicyRejected, match='foreign distribution'):
            _inspect_wheel(store, item, policy)
