"""Local, strict runtime records; ordinary JSON, never executable serialization."""
from typing import Annotated, Literal
from pydantic import Field
from feature_rl.contracts import ArtifactRef, CommandSpec, CostRecord, StrictModel
from .profiles import RuntimeProfile

SECCOMP_SHA256 = '005f6ae1a0f3f9d1a0c044f83289e2ea54180c97a105b2539422588eac2fde44'

class EnvironmentError(Exception):
    """Controller/setup failure, never an ordinary candidate verdict."""
class SourceRejected(EnvironmentError): pass
class PolicyRejected(EnvironmentError): pass
class DockerUnavailable(EnvironmentError): pass
class CleanupUnverified(EnvironmentError): pass
class SourceUnavailable(EnvironmentError): pass
class DependencyUnavailable(EnvironmentError):
    """Trusted resolution cannot establish a reproducible candidate dependency closure."""
class CpuBudgetExceeded(EnvironmentError): pass
class EvidencePublicationFailed(EnvironmentError):
    """Bounded pending publication; no execution rerun is needed to retry storage."""
    failure_category='infrastructure'
    def __init__(self,message,*,payload,kind,visibility):
        super().__init__(message)
        self.payload=payload;self.kind=kind;self.visibility=visibility
        import json,hashlib
        self.sha256=hashlib.sha256(payload).hexdigest()
        try:value=json.loads(payload)
        except (ValueError,UnicodeError):value={}
        self.cleanup_verified=value.get('cleanup_verified',False) if isinstance(value,dict) else False
        self.saved_source=value.get('extra',{}).get('saved_source') if isinstance(value,dict) else None


class SandboxPolicy(StrictModel):
    version: Literal['docker-python-v3'] = 'docker-python-v3'
    image: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}$')] | None = None
    seccomp_sha256: Literal[SECCOMP_SHA256] = SECCOMP_SHA256
    platform: Literal['linux/arm64', 'linux/amd64']
    profile: RuntimeProfile | None = None
    cpus: Annotated[float,Field(ge=0.1,le=2.0)] = 0.5
    cpu_seconds: Annotated[float,Field(ge=1,le=600)] = 60.0
    memory_bytes: Annotated[int,Field(ge=64*1024*1024,le=1024*1024*1024)] = 512*1024*1024
    pids: Annotated[int,Field(ge=8,le=128)] = 64
    disk_bytes: Annotated[int,Field(ge=8*1024*1024,le=256*1024*1024)] = 128*1024*1024
    output_bytes: Annotated[int,Field(gt=0,le=8*1024*1024)] = 2*1024*1024
    stdin_bytes: Annotated[int,Field(gt=0,le=8*1024*1024)] = 1024*1024
    max_source_bytes: Annotated[int,Field(gt=0,le=32*1024*1024)] = 8*1024*1024
    max_archive_bytes: Annotated[int,Field(gt=0,le=64*1024*1024)] = 16*1024*1024
    max_files: Annotated[int,Field(gt=0,le=10000)] = 2000
    max_staging_bytes: Annotated[int,Field(gt=0,le=64*1024*1024)] = 32*1024*1024
    lifecycle_seconds: Annotated[float,Field(gt=0,le=600)] = 120.0
    cleanup_seconds: Annotated[float,Field(gt=0,le=30)] = 10.0
    control_seconds: Annotated[float,Field(gt=0,le=10)] = 5.0
    save_policy: Literal['last-confirmed-source'] = 'last-confirmed-source'


class ProcessObservation(StrictModel):
    argv: tuple[str,...]
    exit_code: int | None
    reason: Literal['exited','timeout','output_limit','cpu_limit','memory_limit','monitor_failure']
    stdout: bytes
    stderr: bytes
    observed_bytes: int
    wall_seconds: float

class ExecutionRequest(StrictModel):
    command: CommandSpec
    stdin: bytes = b''
    save_source: bool = True
    remaining_cpu_seconds: Annotated[float,Field(gt=0)] | None = None

class SavedSource(StrictModel):
    artifact: ArtifactRef
    raw_sha256: Annotated[str,Field(pattern=r"^[0-9a-f]{64}$")]
    tree_sha256: Annotated[str,Field(pattern=r"^[0-9a-f]{64}$")]
    version: Annotated[int,Field(ge=0)]
    saved_at: str

class ExecutionResult(StrictModel):
    operation_id: str
    reason: Literal['completed','command_failed','timeout','output_limit','cpu_limit','memory_limit','source_rejected','source_unavailable','setup_failed','infrastructure_failure']
    failure_category: Literal['none','candidate','infrastructure','unresolved']
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    container_exit_code: int | None
    oom_killed: bool
    maximum_memory_bytes: int
    cleanup_verified: bool
    saved_source: SavedSource
    save_status: Literal['saved','last_confirmed','unchanged']
    evidence: ArtifactRef
    cost: CostRecord

class PreparedEnvironment(StrictModel):
    recipe: ArtifactRef
    policy: ArtifactRef

class WorkspaceHandle(StrictModel):
    workspace_id: Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]

class Ownership(StrictModel):
    operation_id: Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]
    owner_token: Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]
    daemon_id: str
    container_name: str
    container_id: str | None
    phase: Literal['intent','created','running','cleanup_pending','removed']
    binding: dict[str,str]
    saved_source: dict
    created_at: str
    error: str | None = None

class BuildResult(StrictModel):
    source: ArtifactRef
    recipe: ArtifactRef
    policy: ArtifactRef
    dependency_resolution: ArtifactRef
    wheel: ArtifactRef
    wheel_filename: str
    wheel_sha256: Annotated[str,Field(pattern=r"^[0-9a-f]{64}$")]
    source_tree_sha256: Annotated[str,Field(pattern=r"^[0-9a-f]{64}$")]
    cost: CostRecord
    evidence: ArtifactRef
