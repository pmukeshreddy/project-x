"""Click's pinned offline recipe and saved-source workspace API.

No submitted module/build hook is imported by this controller. Worker-produced
bytes remain untrusted and are validated before publication or reuse.
"""
import base64
from datetime import datetime,timezone
import hashlib
import io
import json
import tarfile
import tomllib
import uuid
import zipfile
from pathlib import Path

from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import (ActorRole,AllowedChanges,ArtifactRef,CommandSpec,CostRecord,
    DependencyPin,EnvironmentRecipe,EnvironmentVariable,EvidenceRecord,Provenance,
    ResourceLimits,SeedPolicy,SourcePair,Visibility,NeutralRepair)
from .archive import SourceArchive,SourceFile,safe_path
from .docker import DockerEngine,utc_now,observation_json,stream_process
from .models import (BuildResult,EnvironmentError,ExecutionRequest,ExecutionResult,PolicyRejected,
    PreparedEnvironment,SandboxPolicy,SavedSource,SourceRejected,WorkspaceHandle,SourceUnavailable,REPAIRED_IMAGE,IMAGE,CleanupUnverified,DockerUnavailable,EvidencePublicationFailed)
from .workers import STAGE_CODE
import time

# Exact inert acquisition identities; no solver-visible target-package wheel.
WHEELS={
 'pytest':('9.0.2','pytest-9.0.2-py3-none-any.whl','711ffd45bf766d5264d487b917733b453d917afd2b0ad65223959f59089f875b'),
 'iniconfig':('2.3.0','iniconfig-2.3.0-py3-none-any.whl','f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12'),
 'packaging':('26.0','packaging-26.0-py3-none-any.whl','b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529'),
 'pluggy':('1.6.0','pluggy-1.6.0-py3-none-any.whl','e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746'),
 'pygments':('2.20.0','pygments-2.20.0-py3-none-any.whl','81a9e26dd42fd28a23a2d169d86d7ac03b46e2f8b59ed4698fb4785f946d0176'),
 'flit-core':('3.11.0','flit_core-3.11.0-py3-none-any.whl','fe464c086f630f106c0fc5001ee377980f45938f03f8f0d03da08a4841748541'),
}
ENV=(('PYTHONPATH','/workspace/site:/workspace/deps'),('PYTHONSAFEPATH','1'),('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1'))
DEV_ENV=(('PYTHONPATH','/workspace/source/src:/workspace/deps'),('PYTHONSAFEPATH','1'),('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1'))
REPAIR={'kind':'system-pager-repair-v1','base_manifest':IMAGE,'image_manifest':REPAIRED_IMAGE,'package':'less_590-2.1~deb12u2_arm64.deb','package_sha256':'eb430d92921f98b163031ee3ac81a96110d1f20bd84f67faab06dad50c75d744','binary_sha256':'2dd8d7734f5b43961b1d7243198ffaf1037539b122f2e266fccc9621104ed33b','changes':['COPY /usr/bin/less mode 0755','COPY copyright mode 0644'],'environment_repair':1,'environment_repair_budget':2,'candidate_repair':1,'candidate_repair_budget':4}
DEPS=CommandSpec(argv=('python','-I','-m','pip','--isolated','install','--no-index','--no-deps','--require-hashes','--find-links','/workspace/supply','--target','/workspace/deps','--no-compile','--ignore-installed','-r','/workspace/supply/requirements.txt'),working_directory='/workspace',timeout_seconds=30.0)
BUILD=CommandSpec(argv=('python','-m','pip','--isolated','wheel','--no-build-isolation','--no-deps','--no-index','--wheel-dir','/workspace/built','/workspace/source'),working_directory='/workspace',timeout_seconds=30.0)
INSTALL=CommandSpec(argv=('python','-I','-m','pip','--isolated','install','--no-index','--no-deps','--no-compile','--target','/workspace/site','/workspace/built/click-8.3.3-py3-none-any.whl'),working_directory='/workspace',timeout_seconds=30.0)

class BuildFailed(EnvironmentError):
    """Attributable build outcome; infrastructure failures are never candidate verdicts."""
    def __init__(self,message,evidence,saved_source,*,reason,failure_category,cleanup_verified):
        super().__init__(message)
        self.evidence=evidence;self.saved_source=saved_source;self.reason=reason
        self.failure_category=failure_category;self.cleanup_verified=cleanup_verified

class StageFailure(EnvironmentError):
    def __init__(self,message,reason,category):
        super().__init__(message);self.reason=reason;self.category=category


def failure(exc):
    if isinstance(exc,StageFailure):return exc.reason,exc.category
    if isinstance(exc,SourceRejected):return 'source_rejected','candidate'
    if isinstance(exc,SourceUnavailable):return 'source_unavailable','unresolved'
    return 'infrastructure_failure','infrastructure'


class EnvironmentRuntime:
    def __init__(self,*,store:ArtifactStore,engine:DockerEngine,revision:str):
        if not getattr(engine,'qualified',False):raise PolicyRejected('production boundary qualification required')
        if not isinstance(store,ArtifactStore) or store.role!=ActorRole.CONTROLLER:raise PolicyRejected('controller store required')
        if len(revision) not in (40,64) or any(c not in '0123456789abcdef' for c in revision):raise PolicyRejected('implementation revision required')
        self.store=store;self.engine=engine;self.policy=engine.policy;self.revision=revision
        self.qualification_ref=self.publish(engine.qualification,'sandbox-qualification')
        self.qualification_summary_ref=self.publish({'qualified':True,'policy':self.policy.model_dump(mode='json'),'observations':engine.qualification['observations']},'sandbox-qualification-summary',Visibility.AUTHORING)
        self.repair_ref=self.publish(REPAIR,'neutral-environment-repair',Visibility.AUTHORING)
    def publish(self,value,kind,visibility=Visibility.PRIVATE):
        data=canonical_json(value)
        if len(data)>64*1024*1024:raise EnvironmentError('evidence publication limit')
        try:return self.store.put_bytes(data,kind,visibility)
        except Exception as exc:raise EvidencePublicationFailed('controller evidence publication failed',payload=data,kind=kind,visibility=visibility) from exc
    def retry_publication(self,pending):
        if not isinstance(pending,EvidencePublicationFailed) or len(pending.payload)>64*1024*1024 or hashlib.sha256(pending.payload).hexdigest()!=pending.sha256:raise PolicyRejected('invalid pending publication')
        try:return self.store.put_bytes(pending.payload,pending.kind,pending.visibility)
        except Exception as exc:raise pending from exc
    def read_bytes(self,ref,cap):
        return self.store.get_bytes(ArtifactRef.model_validate(ref),max_envelope_bytes=4*((cap+2)//3)+4096,max_payload_bytes=cap)
    def source(self,ref):
        if ref.kind!='source-archive' or ref.encoding!='bytes':raise SourceRejected('source-archive byte ref required')
        return SourceArchive.read(self.read_bytes(ref,self.policy.max_archive_bytes),self.policy)
    def dependency_bytes(self,pins):
        if len(pins)!=len(WHEELS) or {p.name for p in pins}!=set(WHEELS):raise PolicyRejected('exact six approved dependency pins required')
        result={};total=0
        for pin in pins:
            version,filename,digest=WHEELS[pin.name]
            if pin.version!=version or pin.sha256!=digest or pin.artifact.kind!='dependency-wheel':raise PolicyRejected('unapproved dependency identity')
            data=self.read_bytes(pin.artifact,2*1024*1024);total+=len(data)
            if hashlib.sha256(data).hexdigest()!=digest or total>self.policy.max_staging_bytes:raise PolicyRejected('dependency bytes/aggregate mismatch')
            result[filename]=SourceFile(data,False)
        requirements=''.join(f'{name}=={v[0]} --hash=sha256:{v[2]}\n' for name,v in sorted(WHEELS.items())).encode()
        result['requirements.txt']=SourceFile(requirements,False);return result
    def create_click_recipe(self,baseline,pins,*,source_evidence:EvidenceRecord):
        baseline=ArtifactRef.model_validate(baseline);pins=tuple(DependencyPin.model_validate(x) for x in pins)
        if self.policy.image!=REPAIRED_IMAGE:raise PolicyRejected('Click requires the pinned system pager repair')
        source=self.source(baseline);self.validate_click_manifest(source);self.dependency_bytes(pins)
        if baseline.visibility not in {Visibility.AUTHORING,Visibility.PUBLIC}:raise PolicyRejected('B-only recipe requires authoring/public baseline')
        p=self.policy;policy_ref=self.publish(p.model_dump(mode='json'),'sandbox-policy',Visibility.AUTHORING)
        now=datetime.now(timezone.utc)
        recipe=EnvironmentRecipe(kind='EnvironmentRecipe',schema_version=1,visibility=Visibility.AUTHORING,
            provenance=Provenance(producer='feature_rl.environments',producer_version='1',created_at=now,inputs=(baseline,policy_ref,*[pin.artifact for pin in pins]),evidence=(source_evidence,)),
            costs=(CostRecord(category='construction',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note='Recipe publication; execution measured separately'),),
            image_digest=p.image,interpreter_version='3.12.14',dependencies=pins,setup=(DEPS,BUILD,INSTALL),reset=(DEPS,BUILD,INSTALL),services=(),
            limits=ResourceLimits(wall_seconds=p.lifecycle_seconds,cpu_seconds=p.cpu_seconds,memory_bytes=p.memory_bytes,pids=p.pids,output_bytes=p.output_bytes,disk_bytes=p.disk_bytes,tool_calls=100,input_tokens=1,output_tokens=1),
            neutral_repairs=(NeutralRepair(description='Add pinned Debian less binary and copyright only; environment repair 1/2, candidate repair 1/4',patch=self.repair_ref,neutrality_evidence=(EvidenceRecord(producer='feature_rl.environments trusted package-only image qualification',command=('verify pinned image and base layers','machine-check hardened boundary'),recorded_at=now,exit_status=0,artifacts=(self.repair_ref,self.qualification_summary_ref),revision=self.revision,scope='real_integration'),)),),locale='C.UTF-8',timezone='UTC',environment=tuple(EnvironmentVariable(name=k,value=v) for k,v in ENV),
            randomness=SeedPolicy(algorithm='PYTHONHASHSEED',seeds=(0,),same_cases_within_group=True),network_policy='none',baseline=baseline)
        ref=self.store.put_artifact(recipe);return PreparedEnvironment(recipe=ref,policy=policy_ref)
    def recipe(self,prepared):
        prepared=PreparedEnvironment.model_validate(prepared)
        recipe=self.store.get_artifact(prepared.recipe,max_envelope_bytes=256*1024)
        if not isinstance(recipe,EnvironmentRecipe):raise PolicyRejected('EnvironmentRecipe required')
        policy=SandboxPolicy.model_validate_json(self.read_bytes(prepared.policy,16384))
        if policy!=self.policy or prepared.policy not in recipe.provenance.inputs:raise PolicyRejected('recipe/policy binding mismatch')
        if recipe.image_digest!=policy.image or recipe.interpreter_version!='3.12.14' or recipe.services or recipe.network_policy!='none':raise PolicyRejected('unsupported image/interpreter/service/network')
        if recipe.setup!=(DEPS,BUILD,INSTALL) or recipe.reset!=recipe.setup or len(recipe.neutral_repairs)!=1 or recipe.neutral_repairs[0].patch!=self.repair_ref:raise PolicyRejected('unapproved setup/reset/repair policy')
        if tuple((x.name,x.value) for x in recipe.environment)!=ENV or recipe.locale!='C.UTF-8' or recipe.timezone!='UTC' or recipe.randomness.seeds!=(0,):raise PolicyRejected('unapproved process environment')
        for field in ('memory_bytes','pids','output_bytes','disk_bytes','cpu_seconds'):
            if getattr(recipe.limits,field)!=getattr(policy,field):raise PolicyRejected('recipe resource binding mismatch')
        if recipe.limits.wall_seconds!=policy.lifecycle_seconds:raise PolicyRejected('recipe wall binding mismatch')
        self.dependency_bytes(recipe.dependencies);return recipe
    @staticmethod
    def validate_click_manifest(source):
        try:project=tomllib.loads(source.files['pyproject.toml'].data.decode())
        except (KeyError,ValueError,UnicodeError) as exc:raise PolicyRejected('invalid Click build manifest') from exc
        if project.get('project',{}).get('name')!='click' or project['project'].get('version')!='8.3.3' or project.get('build-system')!={'requires':['flit_core>=3.11,<4'],'build-backend':'flit_core.buildapi'}:raise PolicyRejected('unsupported Click build identity')
    def saved(self,source,version,visibility=Visibility.PRIVATE):
        data=source.to_tar()
        if len(data)>self.policy.max_archive_bytes:raise SourceRejected('saved archive exceeds cap')
        return SavedSource(artifact=self.store.put_bytes(data,'source-archive',visibility),raw_sha256=hashlib.sha256(data).hexdigest(),tree_sha256=source.tree_sha256,version=version,saved_at=utc_now())
    def open_workspace(self,prepared,*,source=None,role='baseline',source_pair=None,allowed_changes=None):
        recipe=self.recipe(prepared);initial=source or recipe.baseline
        if role not in ('baseline','reference','candidate'):raise PolicyRejected('unsupported source role')
        if role=='baseline' and initial!=recipe.baseline:raise PolicyRejected('baseline source mismatch')
        if role=='reference':
            if source_pair is None:raise PolicyRejected('reference requires private M1 source join')
            pair=self.store.get_artifact(source_pair,max_envelope_bytes=512*1024)
            if not isinstance(pair,SourcePair) or pair.baseline!=recipe.baseline or pair.reference!=initial:raise PolicyRejected('M1 source pair mismatch')
        policy=allowed_changes or AllowedChanges(source_roots=('src',),forbidden_paths=(),dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
        policy=AllowedChanges.model_validate(policy)
        if policy.dependencies!='forbidden' or policy.dependency_artifacts or policy.additional_artifact_types:raise PolicyRejected('only source-only fixed-dependency changes supported')
        for path in (*policy.source_roots,*policy.forbidden_paths):safe_path(path)
        if any(root!='src' and not root.startswith('src/') for root in policy.source_roots):raise PolicyRejected('Click source-only roots must stay beneath src; manifests and tests are immutable')
        baseline=self.source(recipe.baseline);tree=self.source(initial);self.validate_click_manifest(tree)
        if role=='candidate':tree.validate_changes(baseline,policy.source_roots,policy.forbidden_paths)
        snapshot=self.saved(tree,0)
        handle=WorkspaceHandle(workspace_id=uuid.uuid4().hex)
        value={'workspace_id':handle.workspace_id,'prepared':prepared.model_dump(mode='json'),'initial':snapshot.model_dump(mode='json'),'saved':snapshot.model_dump(mode='json'),'source_input':initial.model_dump(mode='json'),'source_pair':source_pair.model_dump(mode='json') if source_pair else None,'role':role,'allowed_changes':policy.model_dump(mode='json'),'closed':False,'generation':0}
        with self.engine.state.lock():self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return handle
    def workspace(self,handle):
        handle=WorkspaceHandle.model_validate(handle);value=self.engine.state.read('workspace-'+handle.workspace_id+'.json')
        if value['workspace_id']!=handle.workspace_id or value['closed']:raise PolicyRejected('workspace is closed/mismatched')
        prepared=PreparedEnvironment.model_validate_json(canonical_json(value['prepared']));recipe=self.recipe(prepared)
        saved=SavedSource.model_validate_json(canonical_json(value['saved']));source=self.source(saved.artifact)
        if hashlib.sha256(self.read_bytes(saved.artifact,self.policy.max_archive_bytes)).hexdigest()!=saved.raw_sha256 or source.tree_sha256!=saved.tree_sha256:raise SourceRejected('saved source binding mismatch')
        return value,prepared,recipe,saved,source
    def stage(self,session,source,pins,*,wheel=None):
        files={'source/'+name:entry for name,entry in source.files.items()}
        files.update({'supply/'+name:entry for name,entry in self.dependency_bytes(pins).items()})
        if wheel is not None:files['built/click-8.3.3-py3-none-any.whl']=SourceFile(wheel,False)
        data=SourceArchive(files).to_tar()
        if len(data)>self.policy.max_staging_bytes:raise PolicyRejected('aggregate stage archive cap')
        command=CommandSpec(argv=('python','-I','-c',STAGE_CODE,str(self.policy.max_staging_bytes)),working_directory='/workspace',timeout_seconds=20.0)
        r=session.execute(command,data,staging=True)
        if r.reason!='exited' or r.exit_code!=0:raise EnvironmentError('source/dependency staging failed')
    def binding(self,prepared,saved,phase,value):
        return {'recipe':prepared.recipe.sha256,'policy':prepared.policy.sha256,'source':saved.artifact.sha256,'source_raw':saved.raw_sha256,'tree':saved.tree_sha256,'phase':phase,'revision':self.revision,'workspace_id':value['workspace_id'],'generation':str(value['generation']),'role':value['role'],'source_input':value['source_input']['sha256'],'source_pair':value['source_pair']['sha256'] if value['source_pair'] else 'none','allowed_changes':hashlib.sha256(canonical_json(value['allowed_changes'])).hexdigest()}
    def evidence(self,session,phase,extra=None):
        return self.publish({'phase':phase,'revision':self.revision,'lifecycle_wall_seconds':max(0.0,time.monotonic()-(session.deadline-session.policy.lifecycle_seconds)),'record':session.record.model_dump(mode='json'),'effective':session.effective,'commands':session.receipts,'cleanup_verified':session.cleanup_verified,'maximum_cpu_seconds':session.maximum_cpu_seconds,'maximum_memory_bytes':session.maximum_memory_bytes,'memory_oom_events':session.memory_oom_events,'container_state':session.container_state,'extra':extra},'environment-execution')
    def wheel_bytes(self,data,source):
        if len(data)>self.policy.max_source_bytes:raise SourceRejected('built wheel size limit')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos=z.infolist()
                if len(infos)>self.policy.max_files or sum(x.file_size for x in infos)>self.policy.max_source_bytes+1024*1024:raise SourceRejected('expanded wheel cap')
                names=[safe_path(x.filename) for x in infos]
                if len(set(names))!=len(names):raise SourceRejected('duplicate wheel paths')
                actual={}
                for info,name in zip(infos,names):
                    if name.startswith('click/'):
                        value=z.read(info);actual['src/'+name]=value
                    elif not name.startswith('click-8.3.3.dist-info/'):raise SourceRejected('unexpected wheel root')
                expected={name:file.data for name,file in source.files.items() if name.startswith('src/click/')}
                if actual!=expected:raise SourceRejected('built Click wheel differs from supplied source')
        except (zipfile.BadZipFile,ValueError,OSError) as exc:raise SourceRejected('invalid built wheel') from exc
        return data
    def build_snapshot(self,handle):
        start=time.monotonic()
        self.recover_owned()
        value,prepared,recipe,saved,source=self.workspace(handle)
        s=self.engine.session(binding=self.binding(prepared,saved,'build',value),saved_source=saved.model_dump(mode='json'));error=None;data=None
        try:
            with s:
                self.stage(s,source,recipe.dependencies)
                for command in (DEPS,BUILD):
                    r=s.execute(command,environment=ENV)
                    if r.reason!='exited' or r.exit_code!=0:
                        category='infrastructure' if command==DEPS or r.reason=='monitor_failure' else 'candidate'
                        reason='infrastructure_failure' if r.reason=='monitor_failure' else ('setup_failed' if command==DEPS else ('command_failed' if r.reason=='exited' else r.reason))
                        raise StageFailure('offline '+('dependency setup' if command==DEPS else 'Click build')+' failed',reason,category)
                command=CommandSpec(argv=('python','-I','-c',"import pathlib,sys;p=pathlib.Path('/workspace/built/click-8.3.3-py3-none-any.whl');data=p.read_bytes();assert len(data)<8388608;sys.stdout.buffer.write(data)"),working_directory='/workspace',timeout_seconds=5.0)
                r=s.execute(command,output_limit=self.policy.max_source_bytes)
                if r.reason=='monitor_failure':raise DockerUnavailable('wheel capture monitor failed')
                if r.reason!='exited' or r.exit_code!=0:raise SourceRejected('built wheel capture failed')
                data=self.wheel_bytes(r.stdout,source)
        except BaseException as exc:error=exc
        reason,category=failure(error) if error else ('completed','none')
        evidence=self.evidence(s,'build',{'error':repr(error) if error else None,'reason':reason,'failure_category':category,'wheel_sha256':hashlib.sha256(data).hexdigest() if data is not None else None,'source_tree_sha256':source.tree_sha256,'saved_source':saved.model_dump(mode='json')})
        if error:
            if not isinstance(error,Exception):raise error
            raise BuildFailed(str(error),evidence,saved,reason=reason,failure_category=category,cleanup_verified=s.cleanup_verified) from error
        try:wheel=self.store.put_bytes(data,'snapshot-wheel',Visibility.PRIVATE)
        except Exception as exc:
            pending=EvidencePublicationFailed('built wheel publication failed after verified cleanup',payload=data,kind='snapshot-wheel',visibility=Visibility.PRIVATE)
            pending.cleanup_verified=s.cleanup_verified;pending.saved_source=saved.model_dump(mode='json');pending.build_evidence=evidence
            raise pending from exc
        cost=CostRecord(category='construction',wall_seconds=time.monotonic()-start,cpu_seconds=s.maximum_cpu_seconds,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='Local offline build including setup, capture, cleanup and publication; currency not estimated')
        return BuildResult(source=saved.artifact,recipe=prepared.recipe,policy=prepared.policy,wheel=wheel,wheel_sha256=hashlib.sha256(data).hexdigest(),source_tree_sha256=source.tree_sha256,evidence=evidence,cost=cost)

    def execute(self,handle,request,*,build):
        """Fresh installed-wheel execution; exact saved-source build is mandatory."""
        return self._execute(handle,request,build=BuildResult.model_validate(build),development=False)

    def execute_development(self,handle,request):
        """Fresh source workspace, pinned dependencies, no successful build prerequisite."""
        return self._execute(handle,request,build=None,development=True)

    def _execute(self,handle,request,*,build,development):
        self.recover_owned()
        request=ExecutionRequest.model_validate(request)
        value,prepared,recipe,saved,source=self.workspace(handle);wheel=None
        if not development:
            if build.source!=saved.artifact or build.recipe!=prepared.recipe or build.policy!=prepared.policy or build.source_tree_sha256!=source.tree_sha256:raise PolicyRejected('build/source/recipe/policy mismatch')
            if build.wheel.kind!='snapshot-wheel' or build.evidence.kind!='environment-execution':raise PolicyRejected('build artifact kinds mismatch')
            wheel=self.read_bytes(build.wheel,self.policy.max_source_bytes)
            if hashlib.sha256(wheel).hexdigest()!=build.wheel_sha256:raise SourceRejected('built wheel raw hash mismatch')
            self.wheel_bytes(wheel,source)
            receipt=json.loads(self.read_bytes(build.evidence,32*1024*1024))
            binding=receipt.get('record',{}).get('binding',{});extra=receipt.get('extra',{})
            if receipt.get('phase')!='build' or receipt.get('cleanup_verified') is not True or extra.get('reason')!='completed' or extra.get('error') is not None or extra.get('wheel_sha256')!=build.wheel_sha256 or extra.get('source_tree_sha256')!=source.tree_sha256 or any(binding.get(k)!=v for k,v in {'recipe':prepared.recipe.sha256,'policy':prepared.policy.sha256,'source':saved.artifact.sha256}.items()):
                raise PolicyRejected('successful build receipt binding mismatch')
        if len(request.stdin)>self.policy.stdin_bytes:raise PolicyRejected('command stdin cap')
        phase='development' if development else 'execute'
        s=self.engine.session(binding=self.binding(prepared,saved,phase,value),saved_source=saved.model_dump(mode='json'))
        start=time.monotonic();result=None;error=None;save_status='last_confirmed';next_saved=saved
        environment=DEV_ENV if development else ENV
        try:
            with s:
                self.stage(s,source,recipe.dependencies,wheel=wheel)
                for command in ((DEPS,) if development else (DEPS,INSTALL)):
                    r=s.execute(command,environment=environment)
                    if r.reason!='exited' or r.exit_code!=0:raise StageFailure('offline execution setup failed','infrastructure_failure' if r.reason=='monitor_failure' else 'setup_failed','infrastructure')
                result=s.execute(request.command,request.stdin,environment=environment)
                if result.reason=='exited' and request.save_source:
                    captured=s.export_source();rules=AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
                    captured.validate_changes(source,rules.source_roots,rules.forbidden_paths)
                    if captured.tree_sha256==source.tree_sha256:save_status='unchanged'
                    else:
                        proposed=self.saved(captured,saved.version+1);value['saved']=proposed.model_dump(mode='json');value['generation']+=1
                        self.engine.state.write('workspace-'+handle.workspace_id+'.json',value);next_saved=proposed;save_status='saved'
        except BaseException as exc:error=exc
        if error:reason,category=failure(error)
        elif result.reason=='monitor_failure':reason,category='infrastructure_failure','infrastructure'
        elif result.reason=='exited':reason,category=('command_failed','candidate') if result.exit_code else ('completed','none')
        else:reason,category=result.reason,'candidate'
        evidence=self.evidence(s,phase,{'error':repr(error) if error else None,'reason':reason,'failure_category':category,'saved_source':next_saved.model_dump(mode='json'),'save_status':save_status,'import_policy':'current source first; no installed Click' if development else 'fresh exact-source wheel installed outside source','build_evidence':build.evidence.model_dump(mode='json') if build else None})
        cost=CostRecord(category='execution',wall_seconds=time.monotonic()-start,cpu_seconds=s.maximum_cpu_seconds,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='CPU/memory maximum from Docker cgroup samples; wall includes setup/export/cleanup; no currency estimate')
        if error and not isinstance(error,Exception):raise error
        return ExecutionResult(operation_id=s.record.operation_id,reason=reason,failure_category=category,exit_code=result.exit_code if result else None,stdout=result.stdout if result else b'',stderr=result.stderr if result else b'',
            container_exit_code=s.container_state.get('ExitCode'),oom_killed=bool(s.container_state.get('OOMKilled')) or s.memory_oom_events>0,maximum_memory_bytes=s.maximum_memory_bytes,cleanup_verified=s.cleanup_verified,saved_source=next_saved,save_status=save_status,evidence=evidence,cost=cost)
    def reset(self,handle):
        self.recover_owned()
        with self.engine.state.lock():
            value,prepared,recipe,saved,source=self.workspace(handle)
            value['saved']=value['initial'];value['generation']+=1;self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return SavedSource.model_validate_json(canonical_json(value['saved']))
    def close(self,handle):
        self.recover_owned()
        with self.engine.state.lock():
            value,*_=self.workspace(handle)
            value['closed']=True;value['generation']+=1;self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return SavedSource.model_validate_json(canonical_json(value['saved']))
    def recover_owned(self):return self.engine.recover_owned()
