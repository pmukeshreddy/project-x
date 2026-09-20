"""Profile-pinned offline recipes and saved-source workspace API.

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
from pathlib import Path

from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import (ActorRole,AllowedChanges,ArtifactRef,CommandSpec,CostRecord,
    DependencyPin,EnvironmentRecipe,EnvironmentVariable,EvidenceRecord,Provenance,
    ResourceLimits,SeedPolicy,SourcePair,Visibility)
from .archive import SourceArchive,SourceFile,safe_path
from .docker import DockerEngine,utc_now,observation_json,stream_process
from .models import (BuildResult,EnvironmentError,ExecutionRequest,ExecutionResult,PolicyRejected,
    PreparedEnvironment,SandboxPolicy,SavedSource,SourceRejected,WorkspaceHandle,SourceUnavailable,DependencyUnavailable,CleanupUnverified,DockerUnavailable,EvidencePublicationFailed,CpuBudgetExceeded)
from .workers import STAGE_CODE
from .profiles import dependency_files, validate_recipe_profile
import time

INSTALL_DEPENDENCIES=CommandSpec(argv=('python','-I','-m','pip','--isolated','--disable-pip-version-check','install','--no-index','--no-deps','--require-hashes','--ignore-installed','--no-compile','--find-links','/workspace/supply','--target','/workspace/deps','-r','/workspace/supply/requirements.txt'),working_directory='/workspace',timeout_seconds=30.0)

BUILD=CommandSpec(argv=('python','-m','pip','--isolated','wheel','--no-build-isolation','--no-deps','--no-index','--wheel-dir','/workspace/built','/workspace/source'),working_directory='/workspace',timeout_seconds=30.0)

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
    if isinstance(exc,CpuBudgetExceeded):return 'cpu_limit','candidate'
    if isinstance(exc,StageFailure):return exc.reason,exc.category
    if isinstance(exc,SourceRejected):return 'source_rejected','candidate'
    if isinstance(exc,SourceUnavailable):return 'source_unavailable','unresolved'
    return 'infrastructure_failure','infrastructure'


class EnvironmentRuntime:
    def __init__(self,*,store:ArtifactStore,engine:DockerEngine,revision:str):
        if engine.policy.profile is not None and not getattr(engine,'qualified',False):raise PolicyRejected('production boundary qualification required')
        if not isinstance(store,ArtifactStore) or store.role!=ActorRole.CONTROLLER:raise PolicyRejected('controller store required')
        if len(revision) not in (40,64) or any(c not in '0123456789abcdef' for c in revision):raise PolicyRejected('implementation revision required')
        self.store=store;self.engine=engine;self.policy=engine.policy;self.base_policy=engine.policy;self.revision=revision
        self.profile=self.policy.profile
        if engine.qualified:self._publish_qualification()
    def _publish_qualification(self):
        self.qualification_ref=self.publish(self.engine.qualification,'sandbox-qualification')
        self.qualification_summary_ref=self.publish({'qualified':True,'policy':self.policy.model_dump(mode='json'),'observations':self.engine.qualification['observations']},'sandbox-qualification-summary',Visibility.AUTHORING)
    def bind_policy(self,policy):
        policy=SandboxPolicy.model_validate(policy)
        if policy.profile is None or policy.image is None:raise PolicyRejected('resolved repository runtime required')
        if policy.model_dump(exclude={'image','profile'})!=self.base_policy.model_dump(exclude={'image','profile'}):
            raise PolicyRejected('resolved repository changed sandbox constraints')
        if self.engine.policy!=policy or not self.engine.qualified:
            self.engine.qualified=False;self.engine.qualification=None
            self.engine.policy=policy
            self.engine.qualify_boundary(image=policy.image)
        self.policy=policy;self.profile=policy.profile
        self._publish_qualification()
    def prepare_repository(self,baseline,*,source_evidence,extra_roots=()):
        from .resolution import resolve_repository
        source=self.source(baseline)
        policy,pins=resolve_repository(self,source,extra_roots=extra_roots)
        self.bind_policy(policy)
        return self.create_recipe(baseline,pins,source_evidence=source_evidence)
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
        return dependency_files(self.store,pins,self.policy)
    def create_recipe(self,baseline,pins,*,source_evidence:EvidenceRecord):
        from .images import prepare_runtime_image, validate_baseline
        baseline=ArtifactRef.model_validate(baseline);pins=tuple(DependencyPin.model_validate(x) for x in pins)
        if self.profile is None or not self.engine.qualified:raise PolicyRejected('resolved qualified runtime required')
        source=self.source(baseline);self.profile.validate_source(source);self.dependency_bytes(pins)
        if baseline.visibility not in {Visibility.AUTHORING,Visibility.PUBLIC}:raise PolicyRejected('B-only recipe requires authoring/public baseline')
        image,image_ref=prepare_runtime_image(self,pins)
        construction_evidence=validate_baseline(self,image,baseline,source)
        p=self.policy;policy_ref=self.publish(p.model_dump(mode='json'),'sandbox-policy',Visibility.AUTHORING)
        now=datetime.now(timezone.utc)
        repairs=self.profile.neutral_repairs
        recipe=EnvironmentRecipe(kind='EnvironmentRecipe',schema_version=1,visibility=Visibility.AUTHORING,
            provenance=Provenance(producer='feature_rl.environments',producer_version='1',created_at=now,inputs=tuple(dict.fromkeys((baseline,policy_ref,image_ref,image.context,*(() if self.profile.resolution is None else (self.profile.resolution,)),*(pin.artifact for pin in pins)))),evidence=(source_evidence,
                EvidenceRecord(producer='feature_rl.environments runtime image construction',command=('build pinned runtime image','verify offline baseline dependency closure'),recorded_at=now,exit_status=0,artifacts=(image_ref,construction_evidence),revision=self.revision,scope='real_integration'))),
            costs=(CostRecord(category='construction',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note='Recipe publication; execution measured separately'),),
            image_digest=image.image_digest,runtime_image=image_ref,interpreter_version=self.profile.interpreter_version,dependencies=pins,setup=self.profile.setup,reset=self.profile.setup,services=(),
            limits=ResourceLimits(wall_seconds=p.lifecycle_seconds,cpu_seconds=p.cpu_seconds,memory_bytes=p.memory_bytes,pids=p.pids,output_bytes=p.output_bytes,disk_bytes=p.disk_bytes,tool_calls=100,input_tokens=1,output_tokens=1),
            neutral_repairs=repairs,locale='C.UTF-8',timezone='UTC',environment=tuple(EnvironmentVariable(name=k,value=v) for k,v in self.profile.environment),
            randomness=SeedPolicy(algorithm='PYTHONHASHSEED',seeds=(0,),same_cases_within_group=True),network_policy='none',baseline=baseline)
        validate_recipe_profile(recipe,p,self.store)
        ref=self.store.put_artifact(recipe);return PreparedEnvironment(recipe=ref,policy=policy_ref)
    def recipe(self,prepared,*,bind=True):
        prepared=PreparedEnvironment.model_validate(prepared)
        recipe=self.store.get_artifact(prepared.recipe,max_envelope_bytes=1024*1024)
        if not isinstance(recipe,EnvironmentRecipe):raise PolicyRejected('EnvironmentRecipe required')
        policy=SandboxPolicy.model_validate_json(self.read_bytes(prepared.policy,65536))
        if prepared.policy not in recipe.provenance.inputs:raise PolicyRejected('recipe/policy binding mismatch')
        validate_recipe_profile(recipe,policy,self.store)
        if bind:self.bind_policy(policy)
        return recipe
    def candidate_dependencies(self,prepared,source,rules):
        """Resolve on the controller, then freeze a reusable offline build input."""
        from .catalog import RegistryCatalog
        from .candidates import (DependencyResolution, _candidate_inputs,
            _resolution, _solve, read_dependency_resolution, resolution_identity)
        recipe=self.recipe(prepared,bind=False)
        policy=SandboxPolicy.model_validate_json(self.read_bytes(prepared.policy,131072))
        metadata,rules,environment,manifests,digest,identity,request_identity=_candidate_inputs(
            self.store,prepared,recipe,policy,source,rules)
        candidate_name='candidate-dependencies-'+identity+'.json'
        request_name='dependency-request-'+request_identity+'.json'

        def cached_reference(name):
            try:
                value=self.engine.state.read(name)
            except FileNotFoundError:
                return None
            except (ValueError,RecursionError) as exc:
                raise PolicyRejected('corrupt candidate dependency cache pointer') from exc
            try:
                return ArtifactRef.model_validate_json(canonical_json(value))
            except (ValueError,TypeError,RecursionError) as exc:
                raise PolicyRejected('invalid candidate dependency cache pointer') from exc
        # Serialize acquisition and publication, including concurrent submissions.
        # The request points permanently to its snapshot-qualified resolution;
        # replay never consults current registry state or downloads a wheel again.
        with self.engine.state.lock():
            self.engine._require_clean_owned_state()
            cached=cached_reference(candidate_name)
            if cached is not None:
                return cached,self.read_candidate_dependencies(cached,prepared,source,rules)
            cached=cached_reference(request_name)
            if cached is None:
                available=tuple(dict.fromkeys((*[pin.artifact for pin in recipe.dependencies],
                                                *rules.dependency_artifacts)))
                registry=RegistryCatalog(self,policy,request_identity,available=available)
                selected=_solve(registry.options,metadata,policy.profile,rules,recipe,environment,
                                policy,check_deadline=registry.check_deadline)
                snapshot=registry.freeze()
                frozen=DependencyResolution(request_identity=request_identity,index_snapshot=snapshot,
                    identity=resolution_identity(request_identity,snapshot),
                    wheels=tuple(wheel.item for wheel in selected) if selected is not None else (),
                    failure='no-compatible-closure' if selected is None else None)
                lock_ref=self.publish(frozen.model_dump(mode='json'),'dependency-resolution',Visibility.AUTHORING)
                self.engine.state.write('dependency-resolution-'+frozen.identity+'.json',lock_ref.model_dump(mode='json'))
                self.engine.state.write(request_name,lock_ref.model_dump(mode='json'))
                if selected is None:
                    raise DependencyUnavailable('trusted registry snapshot has no compatible complete dependency closure')
            else:
                lock_ref=cached
                _,selected=read_dependency_resolution(self.store,lock_ref,request_identity,
                    metadata,rules,recipe,policy,environment)
            value=_resolution(prepared,policy,metadata,manifests,digest,identity,lock_ref,selected)
            ref=self.publish(value.model_dump(mode='json'),'candidate-dependency-resolution')
            self.engine.state.write(candidate_name,ref.model_dump(mode='json'))
            return ref,value
    def read_candidate_dependencies(self,ref,prepared,source,rules):
        """Verify retained candidate inputs without resolving again or executing."""
        from .candidates import CandidateResolution, validate_candidate_resolution
        ref=ArtifactRef.model_validate(ref)
        if ref.kind!='candidate-dependency-resolution' or ref.visibility!=Visibility.PRIVATE or ref.encoding!='bytes':
            raise PolicyRejected('private candidate dependency resolution required')
        value=CandidateResolution.model_validate_json(self.read_bytes(ref,self.policy.max_staging_bytes))
        recipe=self.recipe(prepared,bind=False)
        policy=SandboxPolicy.model_validate_json(self.read_bytes(prepared.policy,131072))
        return validate_candidate_resolution(value,self.store,prepared,recipe,policy,source,rules)
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
        policy=allowed_changes or AllowedChanges(source_roots=self.profile.source_roots,forbidden_paths=(),dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
        policy=AllowedChanges.model_validate(policy)
        for path in (*policy.source_roots,*policy.forbidden_paths):safe_path(path)
        self.profile.validate_allowed_changes(policy)
        baseline=self.source(recipe.baseline);tree=self.source(initial)
        if role=='candidate':tree.validate_changes(baseline,policy.source_roots,policy.forbidden_paths)
        snapshot=self.saved(tree,0)
        handle=WorkspaceHandle(workspace_id=uuid.uuid4().hex)
        value={'workspace_id':handle.workspace_id,'prepared':prepared.model_dump(mode='json'),'initial':snapshot.model_dump(mode='json'),'saved':snapshot.model_dump(mode='json'),'source_input':initial.model_dump(mode='json'),'source_pair':source_pair.model_dump(mode='json') if source_pair else None,'role':role,'allowed_changes':policy.model_dump(mode='json'),'closed':False,'generation':0}
        with self.engine.state.lock():self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return handle
    def workspace(self,handle,*,bind=True):
        handle=WorkspaceHandle.model_validate(handle);value=self.engine.state.read('workspace-'+handle.workspace_id+'.json')
        if value['workspace_id']!=handle.workspace_id or value['closed']:raise PolicyRejected('workspace is closed/mismatched')
        prepared=PreparedEnvironment.model_validate_json(canonical_json(value['prepared']))
        saved=SavedSource.model_validate_json(canonical_json(value['saved']));source=self.source(saved.artifact)
        if hashlib.sha256(self.read_bytes(saved.artifact,self.policy.max_archive_bytes)).hexdigest()!=saved.raw_sha256 or source.tree_sha256!=saved.tree_sha256:raise SourceRejected('saved source binding mismatch')
        recipe=self.recipe(prepared,bind=bind)
        return value,prepared,recipe,saved,source
    def stage(self,session,source,*,wheel=None,wheel_filename=None,dependencies=None):
        files={'source/'+name:entry for name,entry in source.files.items()}
        if dependencies is not None:
            policy=self.policy.model_copy(update={'profile':dependencies.profile})
            files.update(('supply/'+name,entry) for name,entry in dependency_files(self.store,dependencies.dependencies,policy).items())
        if wheel is not None:
            if not wheel_filename or safe_path(wheel_filename)!=wheel_filename or '/' in wheel_filename:raise SourceRejected('invalid built wheel filename')
            files['built/'+wheel_filename]=SourceFile(wheel,False)
        data=SourceArchive(files).to_tar()
        if len(data)>self.policy.max_staging_bytes:raise PolicyRejected('aggregate stage archive cap')
        command=CommandSpec(argv=('python','-I','-c',STAGE_CODE,str(self.policy.max_staging_bytes)),working_directory='/workspace',timeout_seconds=20.0)
        r=session.execute(command,data,staging=True)
        if r.reason=='cpu_limit':raise CpuBudgetExceeded('CPU budget exhausted during source/dependency staging')
        if r.reason!='exited' or r.exit_code!=0:raise EnvironmentError('source/dependency staging failed')
    def binding(self,prepared,saved,phase,value):
        return {'recipe':prepared.recipe.sha256,'policy':prepared.policy.sha256,'source':saved.artifact.sha256,'source_raw':saved.raw_sha256,'tree':saved.tree_sha256,'phase':phase,'revision':self.revision,'workspace_id':value['workspace_id'],'generation':str(value['generation']),'role':value['role'],'source_input':value['source_input']['sha256'],'source_pair':value['source_pair']['sha256'] if value['source_pair'] else 'none','allowed_changes':hashlib.sha256(canonical_json(value['allowed_changes'])).hexdigest()}
    def evidence(self,session,phase,extra=None):
        return self.publish({'phase':phase,'revision':self.revision,'lifecycle_wall_seconds':max(0.0,time.monotonic()-(session.deadline-session.policy.lifecycle_seconds)),'record':session.record.model_dump(mode='json'),'effective':session.effective,'commands':session.receipts,'cleanup_verified':session.cleanup_verified,'maximum_cpu_seconds':session.maximum_cpu_seconds,'effective_cpu_seconds':session.effective_cpu_seconds,'maximum_memory_bytes':session.maximum_memory_bytes,'memory_oom_events':session.memory_oom_events,'container_state':session.container_state,'extra':extra},'environment-execution')
    def wheel_bytes(self,data,source,filename,*,profile=None):
        from .wheels import validate_wheel
        return validate_wheel(data,source,filename,self.policy if profile is None else self.policy.model_copy(update={'profile':profile}))

    def capture_wheel(self,session,source,*,profile=None):
        from .wheels import CAPTURE_WHEEL_CODE
        command=CommandSpec(argv=('python','-I','-c',CAPTURE_WHEEL_CODE,str(self.policy.max_source_bytes)),working_directory='/workspace',timeout_seconds=5.0)
        result=session.execute(command,output_limit=self.policy.max_source_bytes+20*1024,artifact_capture=True)
        if result.reason=='monitor_failure':raise DockerUnavailable('wheel capture monitor failed')
        if result.reason!='exited' or result.exit_code!=0:raise SourceRejected('built wheel capture failed')
        try:
            with tarfile.open(fileobj=io.BytesIO(result.stdout),mode='r:') as archive:
                member=archive.next()
                if (member is None or not member.isfile() or member.issparse()
                        or member.size<0 or member.size>self.policy.max_source_bytes
                        or safe_path(member.name)!=member.name or '/' in member.name):
                    raise SourceRejected('invalid built wheel capture member')
                data=archive.extractfile(member).read(member.size+1)
                if len(data)!=member.size or archive.next() is not None:
                    raise SourceRejected('built wheel capture must contain one complete file')
                return member.name,self.wheel_bytes(data,source,member.name,profile=profile)
        except (tarfile.TarError,ValueError,OSError) as exc:
            raise SourceRejected('invalid built wheel capture archive') from exc

    def build_snapshot(self,handle):
        start=time.monotonic()
        self.recover_owned()
        value,prepared,recipe,saved,source=self.workspace(handle)
        rules=AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
        resolution_ref,resolution=self.candidate_dependencies(prepared,source,rules)
        profile=resolution.profile
        binding={**self.binding(prepared,saved,'build',value),'dependency_resolution':resolution_ref.sha256}
        s=self.engine.session(binding=binding,saved_source=saved.model_dump(mode='json'),image=recipe.image_digest);error=None;data=None;wheel_filename=None
        try:
            with s:
                self.stage(s,source,dependencies=resolution)
                for command in (INSTALL_DEPENDENCIES,BUILD):
                    r=s.execute(command,environment=profile.environment)
                    if r.reason!='exited' or r.exit_code!=0:
                        dependency_setup=command==INSTALL_DEPENDENCIES
                        category='infrastructure' if dependency_setup or r.reason=='monitor_failure' else 'candidate'
                        reason='infrastructure_failure' if r.reason=='monitor_failure' else ('setup_failed' if dependency_setup else ('command_failed' if r.reason=='exited' else r.reason))
                        raise StageFailure('offline '+('dependency setup' if dependency_setup else 'repository build')+' failed',reason,category)
                from .images import CHECK_CODE
                check=s.execute(CommandSpec(argv=('python','-I','-c',CHECK_CODE,'/workspace/built','/workspace/deps'),
                    working_directory='/workspace',timeout_seconds=30),
                    canonical_json(profile.model_dump(mode='json',exclude={'neutral_repairs'})))
                if check.reason=='monitor_failure':
                    raise DockerUnavailable('dependency verification monitor failed')
                if check.reason!='exited' or check.exit_code!=0:
                    raise SourceRejected('built project dependencies differ from the frozen runtime closure')
                wheel_filename,data=self.capture_wheel(s,source,profile=profile)
        except BaseException as exc:error=exc
        reason,category=failure(error) if error else ('completed','none')
        evidence=self.evidence(s,'build',{'dependency_resolution':resolution_ref.model_dump(mode='json'),'error':repr(error) if error else None,'reason':reason,'failure_category':category,'wheel_sha256':hashlib.sha256(data).hexdigest() if data is not None else None,'wheel_filename':wheel_filename,'source_tree_sha256':source.tree_sha256,'saved_source':saved.model_dump(mode='json')})
        if error:
            if not isinstance(error,Exception):raise error
            raise BuildFailed(str(error),evidence,saved,reason=reason,failure_category=category,cleanup_verified=s.cleanup_verified) from error
        try:wheel=self.store.put_bytes(data,'snapshot-wheel',Visibility.PRIVATE)
        except Exception as exc:
            pending=EvidencePublicationFailed('built wheel publication failed after verified cleanup',payload=data,kind='snapshot-wheel',visibility=Visibility.PRIVATE)
            pending.cleanup_verified=s.cleanup_verified;pending.saved_source=saved.model_dump(mode='json');pending.build_evidence=evidence
            raise pending from exc
        cost=CostRecord(category='construction',wall_seconds=time.monotonic()-start,cpu_seconds=s.maximum_cpu_seconds,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='Local offline build including setup, capture, cleanup and publication; currency not estimated')
        return BuildResult(source=saved.artifact,recipe=prepared.recipe,policy=prepared.policy,dependency_resolution=resolution_ref,wheel=wheel,wheel_filename=wheel_filename,wheel_sha256=hashlib.sha256(data).hexdigest(),source_tree_sha256=source.tree_sha256,evidence=evidence,cost=cost)

    def execute(self,handle,request,*,build):
        """Fresh installed-wheel execution; exact saved-source build is mandatory."""
        return self._execute(handle,request,build=BuildResult.model_validate(build),development=False)

    def execute_development(self,handle,request):
        """Fresh source workspace, pinned dependencies, no successful build prerequisite."""
        return self._execute(handle,request,build=None,development=True)

    def _execute(self,handle,request,*,build,development):
        self.recover_owned()
        request=ExecutionRequest.model_validate(request)
        if request.remaining_cpu_seconds is not None and request.remaining_cpu_seconds>self.policy.cpu_seconds:
            raise PolicyRejected('request CPU cap exceeds admitted policy')
        value,prepared,recipe,saved,source=self.workspace(handle);wheel=None
        rules=AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
        resolution_ref=resolution=None;dependency_failure=None
        if development:
            try:resolution_ref,resolution=self.candidate_dependencies(prepared,source,rules)
            except (SourceRejected,DependencyUnavailable) as exc:
                # Incomplete declarations remain editable using baseline tools.
                # Retain the controller outcome so evidence replay stays offline.
                dependency_failure={'type':type(exc).__name__,'source_tree_sha256':source.tree_sha256,
                                    'reason':str(exc)}
        else:
            resolution_ref=build.dependency_resolution
            resolution=self.read_candidate_dependencies(resolution_ref,prepared,source,rules)
        profile=resolution.profile if resolution is not None else self.profile
        if not development:
            if build.source!=saved.artifact or build.recipe!=prepared.recipe or build.policy!=prepared.policy or build.source_tree_sha256!=source.tree_sha256:raise PolicyRejected('build/source/recipe/policy mismatch')
            if build.wheel.kind!='snapshot-wheel' or build.evidence.kind!='environment-execution':raise PolicyRejected('build artifact kinds mismatch')
            wheel=self.read_bytes(build.wheel,self.policy.max_source_bytes)
            if hashlib.sha256(wheel).hexdigest()!=build.wheel_sha256:raise SourceRejected('built wheel raw hash mismatch')
            self.wheel_bytes(wheel,source,build.wheel_filename,profile=profile)
            receipt=json.loads(self.read_bytes(build.evidence,32*1024*1024))
            binding=receipt.get('record',{}).get('binding',{});extra=receipt.get('extra',{})
            if receipt.get('phase')!='build' or receipt.get('cleanup_verified') is not True or extra.get('reason')!='completed' or extra.get('error') is not None or extra.get('wheel_sha256')!=build.wheel_sha256 or extra.get('wheel_filename')!=build.wheel_filename or extra.get('source_tree_sha256')!=source.tree_sha256 or any(binding.get(k)!=v for k,v in {'recipe':prepared.recipe.sha256,'policy':prepared.policy.sha256,'source':saved.artifact.sha256,'dependency_resolution':resolution_ref.sha256}.items()) or extra.get('dependency_resolution')!=resolution_ref.model_dump(mode='json'):
                raise PolicyRejected('successful build receipt binding mismatch')
        if len(request.stdin)>self.policy.stdin_bytes:raise PolicyRejected('command stdin cap')
        phase='development' if development else 'execute'
        binding={**self.binding(prepared,saved,phase,value),'dependency_resolution':resolution_ref.sha256 if resolution_ref else 'baseline-tools'}
        s=self.engine.session(binding=binding,saved_source=saved.model_dump(mode='json'),cpu_seconds=request.remaining_cpu_seconds,image=recipe.image_digest)
        start=time.monotonic();result=None;error=None;save_status='last_confirmed';next_saved=saved
        environment=profile.development_environment if development else profile.environment
        try:
            with s:
                self.stage(s,source,wheel=wheel,wheel_filename=build.wheel_filename if build else None,dependencies=resolution)
                setup=(INSTALL_DEPENDENCIES,) if resolution is not None else (recipe.setup[0],)
                if not development:setup=(*setup,profile.setup[2])
                for command in setup:
                    r=s.execute(command,environment=environment)
                    if r.reason=='cpu_limit':raise CpuBudgetExceeded('CPU budget exhausted during offline execution setup')
                    if r.reason!='exited' or r.exit_code!=0:raise StageFailure('offline execution setup failed','infrastructure_failure' if r.reason=='monitor_failure' else 'setup_failed','infrastructure')
                result=s.execute(request.command,request.stdin,environment=environment)
                if result.reason=='exited' and request.save_source:
                    captured=s.export_source().without_pytest_cache(source);rules=AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
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
        evidence=self.evidence(s,phase,{'dependency_resolution':resolution_ref.model_dump(mode='json') if resolution_ref else None,'dependency_failure':dependency_failure,'error':repr(error) if error else None,'reason':reason,'failure_category':category,'saved_source':next_saved.model_dump(mode='json'),'save_status':save_status,'import_policy':'current source first; no installed target package' if development else 'fresh exact-source wheel installed outside source','build_evidence':build.evidence.model_dump(mode='json') if build else None})
        cost=CostRecord(category='execution',wall_seconds=time.monotonic()-start,cpu_seconds=s.maximum_cpu_seconds,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='CPU/memory maximum from Docker cgroup samples; wall includes setup/export/cleanup; no currency estimate')
        if error and not isinstance(error,Exception):raise error
        return ExecutionResult(operation_id=s.record.operation_id,reason=reason,failure_category=category,exit_code=result.exit_code if result else None,stdout=result.stdout if result else b'',stderr=result.stderr if result else b'',
            container_exit_code=s.container_state.get('ExitCode'),oom_killed=bool(s.container_state.get('OOMKilled')) or s.memory_oom_events>0,maximum_memory_bytes=s.maximum_memory_bytes,cleanup_verified=s.cleanup_verified,saved_source=next_saved,save_status=save_status,evidence=evidence,cost=cost)
    def reset(self,handle):
        self.recover_owned()
        with self.engine.state.lock():
            self.engine._require_clean_owned_state()
            value,prepared,recipe,saved,source=self.workspace(handle,bind=False)
            value['saved']=value['initial'];value['generation']+=1;self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return SavedSource.model_validate_json(canonical_json(value['saved']))
    def close(self,handle):
        self.recover_owned()
        with self.engine.state.lock():
            self.engine._require_clean_owned_state()
            value,*_=self.workspace(handle,bind=False)
            value['closed']=True;value['generation']+=1;self.engine.state.write('workspace-'+handle.workspace_id+'.json',value)
        return SavedSource.model_validate_json(canonical_json(value['saved']))
    def recover_owned(self):return self.engine.recover_owned()
