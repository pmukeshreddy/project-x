"""Fixed source-editing protocol. All file and process actions execute inside M3."""
from typing import Annotated, Literal, Union
from pydantic import Field, TypeAdapter
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import ExecutionRequest
from feature_rl.verifiers.language import decode_json

HARNESS = 'feature-rl-source-v1'
INSTRUCTIONS = '''Return exactly one JSON action, without markdown:
{"action":"read","path":"src/click/__init__.py"}
{"action":"write","path":"src/click/example.py","content":"Python source"}
{"action":"command","argv":["python","-c","..."],"stdin":"","timeout_seconds":10.0}
{"action":"public_test","argv":["python","-m","pytest"],"stdin":"","timeout_seconds":10.0}
{"action":"submit"}
Paths are relative to /workspace/source. Every command uses a fresh isolated worker;
only confirmed source edits persist. Use public checks; private grading is unavailable.
Each stdout/stderr message shows at most 65536 bytes and reports omitted byte counts.
Full bounded worker outputs remain in the controller's execution receipt.
Submit the current saved source when finished. The environment and public request follow.
'''
PathText = Annotated[str, Field(min_length=1,max_length=1024)]

class Read(c.StrictModel):
    action: Literal['read']
    path: PathText
class Write(c.StrictModel):
    action: Literal['write']
    path: PathText
    content: Annotated[str,Field(max_length=262144)]
class Command(c.StrictModel):
    action: Literal['command','public_test']
    argv: Annotated[tuple[Annotated[str,Field(min_length=1,max_length=32768)],...],Field(min_length=1,max_length=128)]
    stdin: Annotated[str,Field(max_length=262144)] = ''
    timeout_seconds: Annotated[float,Field(gt=0,le=120)] = 30.0
class Submit(c.StrictModel):
    action: Literal['submit']

_ACTION = TypeAdapter(Annotated[Union[Read,Write,Command,Submit],Field(discriminator='action')])

def parse_action(text):
    if not isinstance(text,str) or len(text.encode()) > 512*1024:
        raise ValueError('action byte limit exceeded')
    # Duplicate JSON keys are rejected by the same closed parser as M4.
    return _ACTION.validate_json(canonical_json(decode_json(text.encode(),512*1024)))

# This trusted script is passed as argv data to M3, never evaluated on the host.
_FILE_TOOL = '''import json,pathlib,sys
x=json.load(sys.stdin)
root=pathlib.Path('/workspace/source')
p=(root/x['path']).resolve()
if not p.is_relative_to(root) or p==root: raise ValueError('outside source workspace')
if x['action']=='read':
    with p.open('rb') as f:
        b=f.read(262145)
    if len(b)>262144: raise ValueError('read output limit')
    sys.stdout.buffer.write(b)
else:
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(x['content'],encoding='utf-8')
'''

def execution_request(action, remaining_wall):
    if remaining_wall <= 0: raise ValueError('episode wall budget exhausted')
    if isinstance(action,Submit): return None
    if isinstance(action,(Read,Write)):
        if action.path.startswith('/') or '\\' in action.path or '..' in action.path.split('/'):
            raise ValueError('unsafe source path')
        argv=('python','-I','-c',_FILE_TOOL)
        stdin=canonical_json(action.model_dump(mode='json'))
        timeout=min(30.,remaining_wall)
    else:
        argv=action.argv;stdin=action.stdin.encode();timeout=min(action.timeout_seconds,remaining_wall)
    return ExecutionRequest(command=c.CommandSpec(argv=argv,working_directory='/workspace/source',
        timeout_seconds=float(timeout)),stdin=stdin,save_source=True)


ACTION_FORMAT = 'actions-v1'


def protocol_payload():
    """Canonical public declaration of the actual fixed tool/action implementation."""
    import hashlib
    return canonical_json({'version':'m7-tool-protocol-v1','harness':HARNESS,
        'action_format':ACTION_FORMAT,'schema':_ACTION.json_schema(),'instructions':INSTRUCTIONS,
        'file_tool_sha256':hashlib.sha256(_FILE_TOOL.encode()).hexdigest(),
        'workspace':'/workspace/source','execution':'reviewed-m3-source-worker',
        'action_bytes':512*1024,'feedback_bytes':65536})


def validate_protocol(store,ref,*,action_format):
    if action_format!=ACTION_FORMAT or ref.kind!='m7-tool-protocol' or ref.visibility!=c.Visibility.PUBLIC:
        raise ValueError('Actual public runner tool/action protocol required')
    if store.get_bytes(ref,max_envelope_bytes=131072,max_payload_bytes=65536)!=protocol_payload():
        raise ValueError('Frozen tools differ from actual runner action schema/instructions')
