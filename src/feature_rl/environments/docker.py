"""Owned Docker lifecycle. The Docker socket is used only by the controller."""
import base64
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import time
import uuid
from urllib.parse import quote

from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import ActorRole,CommandSpec
from .archive import SourceArchive,SourceFile
from .models import (CleanupUnverified,DockerUnavailable,EnvironmentError,Ownership,
                     PolicyRejected,ProcessObservation,SandboxPolicy,SECCOMP_SHA256,SourceRejected,SourceUnavailable,IMAGE,REPAIRED_IMAGE,CpuBudgetExceeded)


def utc_now():return datetime.now(timezone.utc).isoformat()

def observation_json(r):
    return dict(argv=list(r.argv),exit_code=r.exit_code,reason=r.reason,observed_bytes=r.observed_bytes,
                wall_seconds=r.wall_seconds,stdout_b64=base64.b64encode(r.stdout).decode(),stderr_b64=base64.b64encode(r.stderr).decode())

class StateDirectory:
    """Private descriptor-relative durable records; never container-mounted."""
    def __init__(self,path):
        self.path=path.absolute()
        self.store=ArtifactStore(self.path,ActorRole.CONTROLLER)
    @contextmanager
    def directory(self):
        # Public store creation already verifies ancestors; independently walk for state IO.
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in self.path.parts[1:]:
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
            yield fd
        finally:os.close(fd)
    @contextmanager
    def lock(self):
        with self.directory() as directory:
            fd=os.open('runtime.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):raise EnvironmentError('unsafe state lock')
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:raise EnvironmentError('runtime operation already active') from exc
                yield
            finally:os.close(fd)
    def write(self,name,value):
        if '/' in name or not name.endswith('.json'):raise EnvironmentError('unsafe state record name')
        data=canonical_json(value)
        if len(data)>2*1024*1024:raise EnvironmentError('state record size limit')
        temp='pending-'+uuid.uuid4().hex
        with self.directory() as directory:
            fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=directory)
            try:
                with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
                os.replace(temp,name,src_dir_fd=directory,dst_dir_fd=directory);os.fsync(directory)
            finally:
                try:os.unlink(temp,dir_fd=directory)
                except FileNotFoundError:pass
    def read(self,name):
        if '/' in name:raise EnvironmentError('unsafe state record name')
        with self.directory() as directory:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            with os.fdopen(fd,'rb') as f:
                st=os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size>2*1024*1024:raise EnvironmentError('unsafe state record')
                data=f.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise EnvironmentError('state read limit')
        value=json.loads(data)
        if canonical_json(value)!=data:raise EnvironmentError('noncanonical state record')
        return value
    def names(self):
        with self.directory() as d:return sorted(os.listdir(d))


def stream_process(argv,stdin,deadline,output_limit,monitor=None):
    """Bound both pipes and nonblocking stdin; EOF never replaces process exit."""
    start=time.monotonic();out=[bytearray(),bytearray()];observed=0;offset=0;reason='exited'
    proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          start_new_session=True,env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR'}})
    sel=selectors.DefaultSelector();next_monitor=0.0
    try:
        for i,pipe in enumerate((proc.stdout,proc.stderr)):
            os.set_blocking(pipe.fileno(),False);sel.register(pipe,selectors.EVENT_READ,i)
        if stdin:
            os.set_blocking(proc.stdin.fileno(),False);sel.register(proc.stdin,selectors.EVENT_WRITE,2)
        else:proc.stdin.close()
        while True:
            now=time.monotonic()
            if now>=deadline:reason='timeout';break
            if monitor and now>=next_monitor:
                verdict=monitor()
                if verdict:reason=verdict;break
                next_monitor=time.monotonic()+0.2
            if proc.poll() is not None and not any(k.data in (0,1) for k in sel.get_map().values()):break
            for key,_ in sel.select(min(.025,max(0,deadline-time.monotonic()))):
                if key.data==2:
                    try:n=os.write(key.fd,stdin[offset:offset+65536]);offset+=n
                    except BrokenPipeError:offset=len(stdin)
                    except BlockingIOError:continue
                    if offset==len(stdin):sel.unregister(key.fileobj);key.fileobj.close()
                else:
                    try:data=os.read(key.fd,65536)
                    except BlockingIOError:continue
                    if not data:sel.unregister(key.fileobj);key.fileobj.close();continue
                    keep=max(0,output_limit-sum(map(len,out)));out[key.data].extend(data[:keep]);observed+=len(data)
                    if observed>output_limit:reason='output_limit';break
            if reason!='exited':break
    finally:
        sel.close()
        # Always terminate our CLI group, even when its leader already exited.
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        proc.wait(timeout=2)
        for pipe in (proc.stdin,proc.stdout,proc.stderr):
            if not pipe.closed:pipe.close()
    return ProcessObservation(argv=tuple(argv),exit_code=proc.returncode,reason=reason,
        stdout=bytes(out[0]),stderr=bytes(out[1]),observed_bytes=observed,wall_seconds=time.monotonic()-start)

class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path,timeout):super().__init__('localhost',timeout=timeout);self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)

class DockerEngine:
    def __init__(self,*,state_root:Path,socket_path:Path,policy:SandboxPolicy):
        self.policy=SandboxPolicy.model_validate(policy);self.state=StateDirectory(state_root)
        if not socket_path.is_absolute():raise PolicyRejected('Docker socket must be absolute')
        self.socket_path=str(socket_path)
        docker=shutil.which('docker')
        if docker is None:raise DockerUnavailable('docker CLI unavailable')
        self.docker=docker
        self.profile=Path(__file__).with_name('seccomp.json')
        data=self.profile.read_bytes()
        if hashlib.sha256(data).hexdigest()!=SECCOMP_SHA256:raise PolicyRejected('bundled seccomp profile changed')
        self.profile_data=json.loads(data)
        # Separate empty CLI config prevents ambient credential-helper/plugin configuration.
        config=self.state.path/'docker-client';ArtifactStore(config,ActorRole.CONTROLLER)
        self.base=[docker,'--config',str(config),'--host','unix://'+self.socket_path]
        r=stream_process(self.base+['info','--format','{{json .}}'],b'',time.monotonic()+policy.control_seconds,1024*1024)
        if r.reason!='exited' or r.exit_code!=0:raise DockerUnavailable(r.stderr.decode(errors='replace')[:2000])
        try:info=json.loads(r.stdout);self.daemon_id=info['ID']
        except (ValueError,KeyError) as exc:raise DockerUnavailable('invalid daemon identity') from exc
        architecture={'aarch64':'arm64','arm64':'arm64','x86_64':'amd64','amd64':'amd64'}.get(info.get('Architecture'))
        if info.get('OSType')!='linux' or architecture!=self.policy.platform.split('/')[1]:raise PolicyRejected('Docker host must match the configured Linux runtime platform')
        self.info=info;self.qualified=False;self.qualification=None

    def session(self,*,binding,saved_source,cpu_seconds=None):return DockerSession(self,binding,saved_source,cpu_seconds=cpu_seconds)

    def http(self,method,path,*,deadline,cap):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Docker HTTP deadline')
        conn=UnixHTTP(self.socket_path,min(remaining,1.0))
        try:
            conn.request(method,'/v1.47'+path);response=conn.getresponse();out=bytearray()
            if response.length is not None and response.length>cap:raise EnvironmentError('Docker HTTP response limit')
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Docker HTTP deadline')
                if conn.sock:conn.sock.settimeout(min(remaining,1.0))
                data=response.read1(min(65536,cap+1-len(out)))
                if not data:break
                out.extend(data)
                if len(out)>cap:raise EnvironmentError('Docker HTTP response limit')
            return response.status,bytes(out)
        finally:conn.close()

    def recover_owned(self):
        recovered=[]
        with self.state.lock():
            for name in self.state.names():
                if not name.startswith('operation-') or not name.endswith('.json'):continue
                record=Ownership.model_validate(self.state.read(name))
                if record.phase=='removed':continue
                s=DockerSession(self,record.binding,record.saved_source,record=record)
                s.cleanup();recovered.append({'operation_id':record.operation_id,'cleanup_verified':s.cleanup_verified,'receipts':s.receipts})
        return recovered

    def _require_clean_owned_state(self):
        """Caller holds the operation lock through this check and terminal commit."""
        for name in self.state.names():
            if not name.startswith('operation-') or not name.endswith('.json'):continue
            record=Ownership.model_validate(self.state.read(name))
            if record.phase!='removed':
                raise CleanupUnverified('pending owned cleanup blocks terminal transition; recover and retry: '+record.operation_id)

    def qualify_boundary(self):
        from .probes import BOUNDARY_CODE,check_boundary
        with self.session(binding={'purpose':'trusted-boundary'},saved_source={}) as s:
            r=s.execute(CommandSpec(argv=('python','-I','-c',BOUNDARY_CODE),working_directory='/workspace',timeout_seconds=8.0),check_oom=False)
            if r.reason!='exited' or r.exit_code!=0:raise PolicyRejected('trusted boundary command failed')
            obs=json.loads(r.stdout);check_boundary(obs,self.policy)
        result={'qualified':True,'cleanup_verified':s.cleanup_verified,'observations':obs,'operation_id':s.record.operation_id,'receipts':s.receipts,'effective':s.effective,'policy':self.policy.model_dump(mode='json')}
        self.qualified=True;self.qualification=result
        return result

class DockerSession:
    def __init__(self,engine,binding,saved_source,record=None,cpu_seconds=None):
        self.engine=engine;self.policy=engine.policy;self.receipts=[];self.cleanup_verified=False;self.effective=None
        if record is not None and 'effective_cpu_seconds' in record.binding:
            cpu_seconds=float(record.binding['effective_cpu_seconds'])
        cap=self.policy.cpu_seconds if cpu_seconds is None else cpu_seconds
        if type(cap) not in (float,int) or not math.isfinite(cap) or cap<=0 or (record is None and cap>self.policy.cpu_seconds):
            raise PolicyRejected('CPU cap must be positive, finite and no greater than admitted policy')
        self.effective_cpu_seconds=float(cap)
        binding={**binding,'effective_cpu_seconds':str(self.effective_cpu_seconds)}
        op=uuid.uuid4().hex
        self.record=record or Ownership(operation_id=op,owner_token=uuid.uuid4().hex,daemon_id=engine.daemon_id,
            container_name='feature-rl-m3-'+op,container_id=None,phase='intent',binding=binding,saved_source=saved_source,created_at=utc_now())
        self.remaining_output=self.policy.output_bytes;self.memory_oom_events=0
        self.deadline=time.monotonic()+self.policy.lifecycle_seconds;self.maximum_cpu_seconds=0.0;self.maximum_memory_bytes=0
        self.container_state={};self._lock=None
    @property
    def name(self):return self.record.container_id or self.record.container_name
    def persist(self,**changes):
        self.record=Ownership.model_validate(self.record.model_copy(update=changes))
        self.engine.state.write('operation-'+self.record.operation_id+'.json',self.record.model_dump(mode='json'))
    def control(self,args,*,deadline=None,cap=1024*1024):
        end=min(deadline or self.deadline,time.monotonic()+self.policy.control_seconds)
        r=stream_process(self.engine.base+args,b'',end,cap);self.receipts.append(observation_json(r));return r
    def checked(self,args,**kwargs):
        r=self.control(args,**kwargs)
        if r.reason!='exited' or r.exit_code!=0:raise EnvironmentError('Docker control failed: '+str(args[:2])+': '+r.stderr.decode(errors='replace')[:1500])
        return r
    def __enter__(self):
        self._lock=self.engine.state.lock();self._lock.__enter__()
        try:
            self.persist()
            if 'workspace_id' in self.record.binding:
                workspace=self.engine.state.read('workspace-'+self.record.binding['workspace_id']+'.json')
                if workspace['closed'] or str(workspace['generation'])!=self.record.binding['generation'] or workspace['saved']['artifact']['sha256']!=self.record.binding['source']:
                    raise PolicyRejected('workspace changed before operation admission')
            image=json.loads(self.checked(['image','inspect',self.policy.image]).stdout)[0]
            if image.get('Os')!='linux' or image.get('Architecture')!=self.policy.platform.split('/')[1] or image['Config'].get('Volumes') or image['Config'].get('OnBuild'):
                raise PolicyRejected('image platform/volume/build policy mismatch')
            if self.policy.image==REPAIRED_IMAGE:
                base=json.loads(self.checked(['image','inspect',IMAGE]).stdout)[0]
                layers=image['RootFS']['Layers'];original=base['RootFS']['Layers']
                if layers[:len(original)]!=original or len(layers)!=len(original)+2:
                    raise PolicyRejected('repaired image base layer identity mismatch')
                self.receipts.append({'image_manifest':self.policy.image,'image_inspect_id':image['Id'],'base_manifest':IMAGE,'base_layers':original,'image_layers':layers})
            disk=self.policy.disk_bytes;tmp=min(8*1024*1024,disk//4);shm=1024*1024;workspace=disk-tmp-shm
            args=['create','--name',self.record.container_name,'--label','feature-rl.owner='+self.record.owner_token,'--label','feature-rl.operation='+self.record.operation_id,
                '--platform',self.policy.platform,'--pull','never','--network','none','--ipc','private','--cgroupns','private','--read-only','--user','65534:65534','--cap-drop','ALL',
                '--security-opt','no-new-privileges=true','--security-opt','seccomp='+str(self.engine.profile),'--pids-limit',str(self.policy.pids),'--cpus',str(self.policy.cpus),
                '--memory',str(self.policy.memory_bytes),'--memory-swap',str(self.policy.memory_bytes),'--shm-size',str(shm),'--ulimit','nofile=256:256','--ulimit','core=0:0',
                '--ulimit','fsize='+str(disk)+':'+str(disk),'--log-driver','none','--restart','no',
                '--tmpfs',f'/workspace:rw,noexec,nosuid,nodev,size={workspace},uid=65534,gid=65534,mode=0700',
                '--tmpfs',f'/tmp:rw,noexec,nosuid,nodev,size={tmp},uid=65534,gid=65534,mode=0700',
                '--workdir','/workspace','--env','LANG=C.UTF-8','--env','LC_ALL=C.UTF-8','--env','TZ=UTC','--env','PYTHONDONTWRITEBYTECODE=1','--env','PYTHONHASHSEED=0',
                '--entrypoint','/usr/local/bin/python',self.policy.image,'-I','-c','import time; time.sleep(86400)']
            cid=self.checked(args).stdout.decode().strip()
            if len(cid)!=64 or any(c not in '0123456789abcdef' for c in cid):raise EnvironmentError('invalid Docker container ID')
            self.persist(container_id=cid,phase='created')
            self.effective=json.loads(self.checked(['inspect',cid]).stdout)[0];self.check_effective()
            self.checked(['start',cid]);self.persist(phase='running');return self
        except BaseException:
            try:self.cleanup()
            finally:self._lock.__exit__(None,None,None)
            raise
    def check_effective(self):
        x=self.effective;h=x['HostConfig'];c=x['Config']
        expected={'ReadonlyRootfs':True,'Privileged':False,'NetworkMode':'none','IpcMode':'private','CgroupnsMode':'private','PidMode':'','UTSMode':'',
                  'Memory':self.policy.memory_bytes,'MemorySwap':self.policy.memory_bytes,'PidsLimit':self.policy.pids,'NanoCpus':int(self.policy.cpus*1e9)}
        if any(h.get(k)!=v for k,v in expected.items()) or h.get('CapDrop')!=['ALL'] or h.get('CapAdd') or h.get('Binds') or h.get('Devices') or h.get('VolumesFrom'):
            raise PolicyRejected('effective Docker resource/namespace/mount mismatch')
        if c.get('User')!='65534:65534' or h['LogConfig']['Type']!='none' or h['RestartPolicy']['Name']!='no':raise PolicyRejected('effective user/log/restart mismatch')
        opts=h.get('SecurityOpt',[]);profiles=[o[8:] for o in opts if o.startswith('seccomp=')]
        if len(profiles)!=1 or json.loads(profiles[0])!=self.engine.profile_data or not any(o in ('no-new-privileges','no-new-privileges=true') for o in opts):raise PolicyRejected('effective seccomp/no-new-privileges mismatch')
        disk=self.policy.disk_bytes;tmp=min(8*1024*1024,disk//4);shm=1024*1024
        expected_tmpfs={path:f'rw,noexec,nosuid,nodev,size={size},uid=65534,gid=65534,mode=0700' for path,size in [('/workspace',disk-tmp-shm),('/tmp',tmp)]}
        if x.get('Mounts') or h.get('Tmpfs')!=expected_tmpfs or h.get('ShmSize')!=shm:raise PolicyRejected('unexpected extra mount or tmpfs policy')
        limits={u['Name']:(u['Soft'],u['Hard']) for u in h.get('Ulimits',[])}
        if limits!={'core':(0,0),'nofile':(256,256),'fsize':(disk,disk)}:raise PolicyRejected('effective rlimit mismatch')
        if c['Labels'].get('feature-rl.owner')!=self.record.owner_token:raise PolicyRejected('owner label mismatch')
    def monitor(self):
        try:
            status,data=self.engine.http('GET','/containers/'+self.name+'/stats?stream=false&one-shot=true',deadline=min(self.deadline,time.monotonic()+1),cap=128*1024)
            if status!=200:return 'monitor_failure'
            stats=json.loads(data);cpu=stats['cpu_stats']['cpu_usage']['total_usage']/1e9;memory=stats['memory_stats'].get('usage',0)
            self.maximum_cpu_seconds=max(self.maximum_cpu_seconds,cpu);self.maximum_memory_bytes=max(self.maximum_memory_bytes,memory)
            if cpu>=self.effective_cpu_seconds:return 'cpu_limit'
        except (OSError,ValueError,KeyError,TimeoutError,http.client.HTTPException):return 'monitor_failure'
        return None
    def memory_events(self):
        r=self.control(['exec','--workdir','/',self.name,'python','-I','-c',"print(open('/sys/fs/cgroup/memory.events').read())"],cap=4096)
        if r.reason!='exited' or r.exit_code!=0:return None
        try:return int(dict(line.split() for line in r.stdout.decode().splitlines() if line.strip())['oom_kill'])
        except (ValueError,KeyError):return None

    def execute(self,command,stdin=b'',*,output_limit=None,staging=False,environment=(),check_oom=True):
        command=CommandSpec.model_validate(command)
        if '\x00' in ''.join(command.argv) or len(canonical_json(command.model_dump(mode='json')))>128*1024:raise PolicyRejected('oversized/invalid command')
        cwd=command.working_directory
        if cwd!='/workspace' and (not cwd.startswith('/workspace/') or any(p in ('.','..','') for p in cwd.split('/')[1:])):raise PolicyRejected('command cwd outside workspace')
        cap=self.policy.max_staging_bytes if staging else self.policy.stdin_bytes
        if type(stdin) is not bytes or len(stdin)>cap:raise PolicyRejected('stdin exceeds policy')
        limit=min(self.remaining_output,output_limit or self.policy.output_bytes)
        args=['exec','-i','--workdir',cwd]
        for key,value in environment:args+=['--env',key+'='+value]
        args+=[self.name,*command.argv]
        before=self.memory_events() if check_oom else None
        r=stream_process(self.engine.base+args,stdin,min(self.deadline,time.monotonic()+command.timeout_seconds),limit,self.monitor)
        after=self.memory_events() if check_oom and r.reason=='exited' else None
        if before is not None and after is not None and after>before:
            self.memory_oom_events+=after-before;r=r.model_copy(update={'reason':'memory_limit'})
        if r.reason=='exited':
            verdict=self.monitor()
            if verdict:r=r.model_copy(update={'reason':verdict})
        self.remaining_output=max(0,self.remaining_output-r.observed_bytes)
        receipt=observation_json(r);receipt.update({'stdin_bytes':len(stdin),'stdin_sha256':hashlib.sha256(stdin).hexdigest(),'oom_events_before':before,'oom_events_after':after,'effective_cpu_seconds':self.effective_cpu_seconds})
        self.receipts.append(receipt);return r
    def export_source(self):
        from .workers import EXPORT_CODE
        started=utc_now()
        command=CommandSpec(argv=('python','-I','-c',EXPORT_CODE,str(self.policy.max_source_bytes),str(self.policy.max_files),str(self.policy.max_archive_bytes)),working_directory='/workspace',timeout_seconds=self.policy.control_seconds)
        # Export is an untrusted source submission, not an atomic filesystem claim.
        # Use its own archive cap instead of the ordinary command output cap.
        args=self.engine.base+['exec','-i','--workdir','/',self.name,*command.argv]
        r=stream_process(args,b'',min(self.deadline,time.monotonic()+self.policy.control_seconds),self.policy.max_archive_bytes,self.monitor)
        if r.reason=='exited':
            verdict=self.monitor()
            if verdict:r=r.model_copy(update={'reason':verdict})
        receipt=observation_json(r);receipt['effective_cpu_seconds']=self.effective_cpu_seconds;self.receipts.append(receipt)
        self.receipts.append({'source_capture_started':started,'source_capture_finished':utc_now(),'semantics':'validated last-confirmed capture; not an atomic filesystem instant'})
        if r.reason=='cpu_limit':raise CpuBudgetExceeded('CPU budget exhausted during source capture')
        if r.reason=='monitor_failure':raise DockerUnavailable('source capture monitor unavailable')
        if r.reason=='output_limit' or (r.reason=='exited' and r.exit_code==65):raise SourceRejected('bounded source capture rejected: '+r.stderr.decode(errors='replace')[:1200])
        if r.reason!='exited' or r.exit_code!=0:raise SourceUnavailable('source capture unavailable: '+r.reason)
        return SourceArchive.read(r.stdout,self.policy)
    def cleanup(self):
        end=time.monotonic()+self.policy.cleanup_seconds
        publication_error=None
        try:self.persist(phase='cleanup_pending')
        except Exception as exc:publication_error=exc
        try:
            identity=self.control(['info','--format','{{.ID}}'],deadline=end,cap=4096)
            if identity.reason!='exited' or identity.exit_code!=0 or identity.stdout.decode().strip()!=self.record.daemon_id:
                raise CleanupUnverified('daemon unavailable or identity changed')
            r=self.control(['inspect',self.name],deadline=end)
            if r.reason!='exited':raise CleanupUnverified('inspect unavailable during cleanup')
            if r.exit_code==0:
                value=json.loads(r.stdout)[0]
                if value['Config']['Labels'].get('feature-rl.owner')!=self.record.owner_token or value['Config']['Labels'].get('feature-rl.operation')!=self.record.operation_id:raise CleanupUnverified('container ownership mismatch')
                self.container_state=value['State']
                self.checked(['rm','--force',self.name],deadline=end)
            elif b'No such object:' not in r.stderr and b'No such container:' not in r.stderr:
                raise CleanupUnverified('daemon failure is not absence')
            absent=self.control(['inspect',self.name],deadline=end)
            if absent.reason!='exited' or absent.exit_code==0 or (b'No such object:' not in absent.stderr and b'No such container:' not in absent.stderr):raise CleanupUnverified('owned container absence unverified')
            self.cleanup_verified=True;self.persist(phase='removed',error=None)
            if publication_error:raise EnvironmentError('cleanup verified but state publication failed') from publication_error
        except BaseException as exc:
            try:self.persist(phase='removed' if self.cleanup_verified else 'cleanup_pending',error=type(exc).__name__+': '+str(exc)[:2000])
            except Exception:pass
            if not isinstance(exc,Exception):raise
            raise CleanupUnverified('cleanup or durable cleanup publication failed') from exc
    def __exit__(self,typ,value,tb):
        try:self.cleanup()
        finally:self._lock.__exit__(typ,value,tb)
