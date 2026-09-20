from feature_rl.agents import protocol
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore,canonical_json
import pytest


def test_tools_bind_actual_action_schema_and_renderer(tmp_path):
    store=ArtifactStore(tmp_path/'cas',c.ActorRole.CONTROLLER)
    assert hasattr(protocol,'protocol_payload')
    payload=protocol.protocol_payload()
    ref=store.put_bytes(payload,'m7-tool-protocol',c.Visibility.PUBLIC)
    protocol.validate_protocol(store,ref,action_format=protocol.ACTION_FORMAT)
    import json
    value=json.loads(payload);value['instructions']='different renderer'
    altered=store.put_bytes(canonical_json(value),'m7-tool-protocol',c.Visibility.PUBLIC)
    with pytest.raises(ValueError):protocol.validate_protocol(store,altered,action_format=protocol.ACTION_FORMAT)
