"""Automatic cached-source intake, bounded authoring and immutable construction.

External preparation is never repeated after an unknown attempt. M2/M4 and builder
work use their existing selected Registry jobs and recovery paths. This service
does not qualify, accept or release a task and never manufactures an author output.
"""
import hashlib
import json
from pathlib import Path
import re

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import EnvironmentRuntime
from feature_rl.history import GitHistory
from feature_rl.intake import CachedSourceCatalog, GitHubPullRequestIntake, PullRequestIntakeResult
from feature_rl.requirements import (AuthoringEvidenceResolver, ContractFinalizationInputs,
    GroundedSource, GenerationCandidate, RetrievalPolicy)
from feature_rl.requirements.retrieval import BaselineRetriever, RetrievalRequest
from feature_rl.requirements.service import build_contract_request
from feature_rl.scenarios import ScenarioFinalizationInputs, build_scenario_request
from feature_rl.verifiers import CheckerFinalizationInputs, ControlFinalizationInputs
from feature_rl.verifiers.service import build_checker_request
from feature_rl.verifiers.control_authoring import build_control_request, ControlRecord
from feature_rl.qualification.controls import ATTACKS
from feature_rl.qualification.evidence import unknown_cost, collapse_costs
from feature_rl.registry import Claim, CostObservation, JobSpec
from .authoring_models import (AuthoringSettings, AuthoringBatch, AuthoringCall, ResolverInputs,
    ControlPlan, ControlSlot, AuthoringRequest, AuthoringBudgetExceeded, AuthoringBudgetUnverified)
from .authoring import read_authoring_receipt
from .construction import put, references
from .factory import Factory, FactoryPublicationFailed, FactoryRecoveryRequired
from .locking import candidate_lock
from .models import BuildInputs
from .packaging import checked, document, read_record, read_bytes, typed, MAX_DOCUMENT
from .workflow_models import (FeatureWorkflowSettings, FeatureWorkflowRequest, IntakeSelection,
    PreparationSelection, FeatureStep, FeatureOutcome)


class FeatureRecoveryRequired(FactoryRecoveryRequired):
    def __init__(self,message,claim,*,step,upstream=None):
        super().__init__(message,claim)
        self.step,self.upstream=step,upstream


def overhead():
    return (unknown_cost('construction','Automatic workflow controller overhead only; source, authoring and build costs stay in their original jobs'),)


class FeatureWorkflow:
    def __init__(self,*,factory:Factory,runtime:EnvironmentRuntime,settings:FeatureWorkflowSettings):
        if type(factory) is not Factory or type(runtime) is not EnvironmentRuntime or runtime.store is not factory.store:
            raise TypeError('automatic construction requires the actual same-store Factory and M3 runtime')
        self.factory,self.store,self.registry,self.runtime=factory,factory.store,factory.registry,runtime
        self.settings=checked(FeatureWorkflowSettings,settings)
        config=self.settings
        # The source catalog verifies each selected body. Bind its complete index
        # before creating any job so path changes cannot change selected inputs.
        path=Path(config.cache_manifest_path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_DOCUMENT:
            raise ValueError('source cache manifest must be a bounded regular file')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=config.cache_manifest_sha256:
            raise ValueError('source cache manifest differs from frozen workflow settings')
        catalog=CachedSourceCatalog(Path(config.cache_root),path,max_bytes=config.source_max_bytes)
        self.intake=GitHubPullRequestIntake(store=self.store,catalog=catalog,
            history=GitHistory(Path(config.git_directory),timeout_seconds=config.git_timeout_seconds),
            factory_revision=config.intake_revision)
        manifest=self.store.put_bytes(raw,'m6-feature-source-index',c.Visibility.PRIVATE)
        value={'version':'m6-feature-policy-v1','revision':factory.revision,
            'builder_revision':factory.builder.revision,'runtime_revision':runtime.revision,
            'runtime_policy':document(runtime.base_policy),
            'dependency_catalog':document(runtime.dependency_catalog) if runtime.dependency_catalog is not None else None,
            'settings':document(config),'source_index':document(manifest)}
        self.configuration=put(factory,value,'m6-feature-policy',dependencies=references(value))

    def _spec(self,ref):
        return JobSpec(operation='construct',inputs=(ref,),configuration=self.configuration,
            implementation=self.factory.revision,invocation='m6-feature',attempt_limit=1)

    def construct(self,request:FeatureWorkflowRequest):
        request=checked(FeatureWorkflowRequest,request)
        ref=put(self.factory,request,'m6-feature-request',dependencies=references(document(request)))
        with candidate_lock(self.store,ref):
            job=self.registry.enqueue(self._spec(ref))
            if job.state=='completed':return job.result
            claim=(self.registry.claim(job.job_id,owner='feature_rl.pipeline.FeatureWorkflow',claim_key='feature-'+job.job_id)
                if job.state=='queued' else self.registry.attempts(job.job_id)[-1].claim)
            return self._resume(claim,ref,request)

    def _validated(self,claim):
        claim=checked(Claim,claim);job=self.registry.job(claim.job_id)
        if (len(job.spec.inputs)!=1 or job.spec!=self._spec(job.spec.inputs[0])
                or not any(row.claim==claim for row in self.registry.attempts(job.job_id))):
            raise ValueError('workflow claim/configuration mismatch')
        ref=job.spec.inputs[0]
        return job,ref,read_record(self.store,ref,FeatureWorkflowRequest,'m6-feature-request')

    def recover(self,claim):
        job,ref,request=self._validated(claim)
        if job.state=='completed':return job.result
        with candidate_lock(self.store,ref):return self._resume(claim,ref,request)

    def _resume(self,claim,ref,request):
        try:return self._execute(claim,ref,request)
        except (FactoryRecoveryRequired,FactoryPublicationFailed):raise
        except Exception as exc:
            raise FeatureRecoveryRequired('automatic construction stopped; retain the selected workflow and its phase evidence',
                claim,step='derivation',upstream=exc) from exc

    def _observation(self,claim,key):
        found=[row for row in self.registry.accounting(claim.job_id).observations
            if row.attempt_id==claim.attempt_id and row.observation.source=='m6-feature-'+key]
        if len(found)>1:raise ValueError('ambiguous workflow phase accounting')
        return found[0] if found else None

    def _observe(self,claim,key,refs,costs):
        old=self._observation(claim,key)
        return self.registry.reconcile(claim,CostObservation(source='m6-feature-'+key,
            upstream_attempt_id=claim.attempt_id,revision=1 if old is None else old.observation.revision+1,
            receipts=tuple(dict.fromkeys((*(old.observation.receipts if old else ()),*refs))),costs=collapse_costs(costs)))

    def _step(self,claim,request,key,inputs,execute,*,child=False):
        value={'version':'m6-feature-input-v1','request':document(request),'key':key,'inputs':inputs}
        ref=put(self.factory,value,'m6-feature-input',dependencies=references(value))
        current=self._observation(claim,key)
        if current is not None:
            if ref not in current.observation.receipts:raise ValueError('workflow phase input changed')
            selected=[r for r in current.observation.receipts if r.kind=='m6-feature-step']
            if selected:
                if len(selected)!=1:raise ValueError('multiple selected workflow phase outcomes')
                step=read_record(self.store,selected[0],FeatureStep,'m6-feature-step')
                self._check_step(step)
                return step.output,selected[0]
            if not child:
                raise FeatureRecoveryRequired('external phase has no frozen outcome; do not repeat its incurred work',claim,step=key)
        else:
            costs=overhead()
            if key=='intake':costs=(*costs,unknown_cost('discovery'),unknown_cost('storage'))
            if key=='preparation':costs=(*costs,unknown_cost('execution'),unknown_cost('storage'))
            self._observe(claim,key,(request,ref),costs)
        try:
            output,costs=execute()
        except FactoryPublicationFailed as exc:
            raise FeatureRecoveryRequired('retain actual child publication before continuing workflow',claim,step=key,upstream=exc) from exc
        except Exception as exc:
            raise FeatureRecoveryRequired('workflow phase outcome remains unresolved; no implicit external redispatch',claim,step=key,upstream=exc) from exc
        step=FeatureStep(claim=claim,request=request,key=key,inputs=ref,output=output,costs=collapse_costs(costs))
        payload=canonical_json(document(step))
        return self._publish_step(payload,claim)

    def _check_step(self,step):
        _,ref,request=self._validated(step.claim)
        current=self._observation(step.claim,step.key)
        if step.request!=ref or current is None or step.inputs not in current.observation.receipts:
            raise ValueError('workflow phase lacks its selected input intent')
        inputs=json.loads(read_bytes(self.store,step.inputs,MAX_DOCUMENT,kind='m6-feature-input'))
        if inputs.get('request')!=document(ref) or inputs.get('key')!=step.key:
            raise ValueError('workflow phase changed its request/key')
        output=step.output
        if isinstance(output,IntakeSelection):
            if step.key!='intake' or inputs['inputs']!=document(request):
                raise ValueError('intake selection changed its frozen request')
            pair=typed(self.store,output.source_pair,c.SourcePair)
            candidate=typed(self.store,output.candidate,c.CandidateRecord)
            if (pair.candidate,pair.baseline,pair.reference,pair.provenance_label)!=(
                    output.candidate,output.baseline,output.reference,output.provenance_label):
                raise ValueError('selected intake output changed exact source lineage')
            if candidate.commits!=pair.relationship:raise ValueError('intake candidate/source graph differs')
            if (candidate.repository_family!=request.intake.repository_family
                    or candidate.request_lineage!=request.intake.request_lineage
                    or candidate.repository_url!=request.intake.repository_url
                    or candidate.provenance_label!=request.intake.provenance_label):
                raise ValueError('intake selection differs from the requested repository/family/lineage')
        elif isinstance(output,PreparationSelection):
            from feature_rl.environments.profiles import validate_recipe_profile
            selected=IntakeSelection.model_validate_json(canonical_json(inputs['inputs']))
            recipe=typed(self.store,output.environment.recipe,c.EnvironmentRecipe)
            policy=read_record(self.store,output.environment.policy,type(self.runtime.policy),'sandbox-policy')
            if (step.key!='preparation' or recipe.baseline!=selected.baseline
                    or output.environment.policy not in recipe.provenance.inputs):
                raise ValueError('preparation changed its selected baseline or runtime policy')
            validate_recipe_profile(recipe,policy,self.store)
            self.runtime.bind_policy(policy)
            raw=read_bytes(self.store,output.context.source,128*1024)
            from feature_rl.requirements.runtime_discovery import parse_discovery, discovery_locator
            observed=parse_discovery(output.context.source,raw)
            data=observed.model_dump(mode='json')
            if (raw.decode()!=output.context.text or data.get('recipe')!=document(output.environment.recipe)
                    or data.get('baseline')!=document(recipe.baseline)
                    or tuple(data.get('entry_points',()))!=output.entry_points
                    or tuple(data.get('supported_observables',()))!=output.supported_observables):
                raise ValueError('workflow discovery lost its actual environment/observation binding')
            if (output.context.locator!=discovery_locator(output.context.source)
                    or {data['build_evidence_sha256'],data['execution_evidence_sha256']}
                    !={ref.sha256 for ref in output.private_evidence}):
                raise ValueError('workflow discovery lost its private execution evidence')
        elif output.artifacts:
            # Each successful/failed child selection comes from the actual
            # source/author/build receipt and remains in its original cost job.
            last=output.artifacts[-1]
            if last.kind not in ('m6-source-disposition','m6-authoring-receipt','m6-construction-result'):
                raise ValueError('workflow child output lacks actual Factory provenance')
            payload=json.loads(read_bytes(self.store,last,MAX_DOCUMENT))
            child=Claim.model_validate_json(canonical_json(payload['claim']))
            if self.registry.job(child.job_id).result!=output:
                raise ValueError('workflow child differs from its selected Registry completion')
            expected=inputs['inputs']
            if last.kind=='m6-source-disposition':
                if step.key!='source' or payload['candidate']!=expected['candidate']:
                    raise ValueError('source phase selected a different candidate')
            elif last.kind=='m6-authoring-receipt':
                request_ref=c.ArtifactRef.model_validate_json(canonical_json(payload['request']))
                selected_request=read_record(self.store,request_ref,AuthoringRequest,'m6-authoring-request')
                if (not step.key.startswith('author-') or document(selected_request.candidate)!=expected['candidate']
                        or document(selected_request.call)!=expected['call']):
                    raise ValueError('authoring phase changed the selected candidate or call')
            else:
                from .construction import read_construction_request
                request_ref=c.ArtifactRef.model_validate_json(canonical_json(payload['request']))
                selected_request=read_construction_request(self.store,request_ref)
                if (step.key!='construction' or document(selected_request.candidate)!=expected['candidate']
                        or document(selected_request.inputs)!=expected['inputs']):
                    raise ValueError('construction phase changed the selected build inputs')
        elif output.disposition==c.Disposition.SUCCESS:
            raise ValueError('workflow cannot synthesize successful child output')

    def _publish_step(self,payload,claim):
        if len(payload)>MAX_DOCUMENT:raise ValueError('workflow phase exceeds publication bound')
        step=FeatureStep.model_validate_json(payload)
        if step.claim!=claim or canonical_json(document(step))!=payload:raise ValueError('workflow phase bytes/claim changed')
        self._check_step(step)
        current=self._observation(claim,step.key).observation
        selected=[ref for ref in current.receipts if ref.kind=='m6-feature-step']
        if selected:
            if len(selected)!=1 or read_bytes(self.store,selected[0],MAX_DOCUMENT)!=payload:
                raise ValueError('workflow phase publication changed selected bytes')
            return step.output,selected[0]
        if current.revision>1 and current.costs!=step.costs:raise ValueError('workflow phase changed selected costs')
        try:
            self._observe(claim,step.key,(step.inputs,),step.costs)
            ref=put(self.factory,step,'m6-feature-step',dependencies=references(document(step)))
            self._observe(claim,step.key,(ref,),step.costs)
        except Exception as exc:
            raise FactoryPublicationFailed('retain exact automatic workflow phase',claim,payload,kind='m6-feature-step') from exc
        return step.output,ref

    def _source(self,request):
        result=self.intake.ingest(request.intake,request.partitions)
        if type(result) is not PullRequestIntakeResult:raise TypeError('actual M1 intake result required')
        return IntakeSelection(candidate=result.candidate,source_pair=result.source_pair,
            request=result.authoring.request_evidence,baseline=result.authoring.baseline,
            reference=result.reference,license_text=result.authoring.license_text,
            provenance_label=result.provenance_label,mixed_paths_for_qualification=result.mixed_paths_for_qualification),(
                unknown_cost('discovery','M1 source/import costs remain on CandidateRecord and source-admission job; workflow overhead unmeasured'),)

    def _prepare(self,selected):
        from feature_rl.requirements import RuntimeDiscoveryService
        candidate=typed(self.store,selected.candidate,c.CandidateRecord)
        pair=typed(self.store,selected.source_pair,c.SourcePair)
        prepared=self.runtime.prepare_repository(selected.baseline,
            source_evidence=candidate.provenance.evidence[0],extra_roots=tuple(entry.path for entry in pair.changed_files))
        discovery=RuntimeDiscoveryService(runtime=self.runtime).discover(prepared)
        value=PreparationSelection(environment=prepared,context=discovery.context,
            entry_points=discovery.observation.entry_points,
            supported_observables=discovery.observation.supported_observables,
            private_evidence=discovery.private_evidence,costs=discovery.costs)
        return value,(*discovery.costs,*overhead(),unknown_cost('storage','Runtime preparation publication overhead unmeasured'))

    def _sources(self,selected,prepared,request):
        source=self.runtime.source(selected.baseline);profile=self.runtime.policy.profile
        profile.validate_source(source)
        text=read_bytes(self.store,selected.request,MAX_DOCUMENT,kind='authoring-request').decode()
        terms=set(re.findall(r'[A-Za-z_]{3,}',text.lower()))-{'the','and','with','this','that','from','true','false','null'}
        ranked=[]
        for path,entry in source.files.items():
            if not any(path==root or path.startswith(root.rstrip('/')+'/') for root in profile.source_roots):continue
            try:lines=entry.data.decode().splitlines()
            except UnicodeError:continue
            if not lines:continue
            scores=[len(terms.intersection(re.findall(r'[A-Za-z_]{3,}',line.lower()))) for line in lines]
            center=max(range(len(lines)),key=lambda index:(scores[index],-index))
            start=max(0,min(center-self.settings.context_lines//2,len(lines)-self.settings.context_lines))
            end=min(len(lines),start+self.settings.context_lines)
            ranked.append((-sum(scores[start:end]),path,start+1,end))
        ranked.sort();spans=[];total=0
        for _,path,start,end in ranked:
            size=len(('\n'.join(source.files[path].data.decode().splitlines()[start-1:end])+'\n').encode())
            if total+size>self.settings.context_bytes:continue
            spans.append(RetrievalRequest(context_id='B_'+str(len(spans)+1),path=path,line_ranges=((start,end),)))
            total+=size
            if len(spans)>=self.settings.context_files:break
        if not spans:raise ValueError('profile has no bounded author-visible source context')
        policy=RetrievalPolicy(allowed_paths=tuple(span.path for span in spans),max_archive_bytes=self.runtime.policy.max_archive_bytes,
            max_files=self.runtime.policy.max_files,max_selected_bytes=self.settings.context_bytes,
            max_spans=self.settings.context_files,max_expanded_bytes=self.runtime.policy.max_source_bytes)
        retrieved=BaselineRetriever(baseline=selected.baseline,
            archive=read_bytes(self.store,selected.baseline,policy.max_archive_bytes),policy=policy).retrieve(tuple(spans))
        sources=(GroundedSource(context_id='FEATURE_REQUEST',role='request',source=selected.request,
            locator='authoring-request:whole',text=text,provenance_label=selected.provenance_label),
            *retrieved.sources,prepared.context,
            *(GroundedSource(context_id='PUBLIC_'+str(index),role='public_check',source=ref,locator='artifact:whole',
                text=read_bytes(self.store,ref,MAX_DOCUMENT,public=True).decode(),provenance_label='existing_obligation')
                for index,ref in enumerate(request.public_checks)))
        resolver=ResolverInputs(request=selected.request,baseline=selected.baseline,runtime_discovery=prepared.context.source,
            public_checks=request.public_checks,retrieval_policy=policy,request_provenance=selected.provenance_label)
        AuthoringEvidenceResolver(store=self.store,**resolver.model_dump()).resolve(sources)
        return sources,resolver,source

    def _author_factory(self,selected):
        conf=self.settings
        settings=AuthoringSettings(backend=conf.backend,m2_revision=conf.m2_revision,m4_revision=conf.m4_revision,
            evidence_scope=conf.evidence_scope,batch=AuthoringBatch(candidates=(selected.candidate,),
                candidate_caps=conf.authoring_caps,batch_caps=conf.authoring_caps,calibration_evidence=conf.calibration_evidence))
        return Factory(store=self.store,registry=self.registry,revision=self.factory.revision,
            builder=self.factory.builder,qualification=self.factory.qualification,authoring=settings)

    def _claimed_at(self,claim):
        after=0;recorded=None
        while recorded is None:
            events=self.registry.events(after=after,limit=1000)
            if not events:raise ValueError('workflow claim event missing')
            for event in events:
                if event.action=='claim' and event.data.get('claim')==document(claim):recorded=event.recorded_at
            after=events[-1].sequence
        return recorded

    def _provenance(self,claim,sources,refs=()):
        # This records controller input derivation only. Actual M2/M4 services add
        # their own fresh provider records/timestamps and never inherit an approval.
        recorded=self._claimed_at(claim)
        visible=tuple(dict.fromkeys(source.source for source in sources))
        return c.Provenance(producer='feature_rl.pipeline.FeatureWorkflow.inputs',producer_version=self.factory.revision,
            created_at=recorded,inputs=tuple(dict.fromkeys((*visible,*refs))),evidence=(c.EvidenceRecord(
                producer='feature_rl.pipeline.FeatureWorkflow.inputs',command=('freeze grounded automatic authoring inputs',),
                recorded_at=recorded,exit_status=0,artifacts=visible,revision=self.factory.revision,scope='source_inspection'),))

    def _child(self,execute):
        try:return execute()
        except FactoryRecoveryRequired as pending:
            if isinstance(pending,FactoryPublicationFailed):raise
            try:return self.factory.recover(pending.claim)
            except Exception as recovery:
                pending.recovery_error=recovery
                raise pending

    def _author(self,claim,request_ref,author,selected,prepared,resolver,sources,inputs,build_request,*,plan=None,attack=None):
        previous=[];last=None
        for attempt in range(3):
            generated=build_request(attempt)
            if previous:
                diagnosis='; '.join(previous)[-4096:]
                generated=generated.model_copy(update={'instruction':generated.instruction+
                    '\nCorrect the retained validation failures without inventing requirements or evidence: '+diagnosis})
                candidate=GenerationCandidate(request=generated,diagnosis=diagnosis,
                    changed_input='Added the exact retained stage failure diagnostics to the request instruction')
            else:candidate=GenerationCandidate(request=generated)
            call=AuthoringCall(source_pair=selected.source_pair,environment=prepared.environment,resolver=resolver,
                generation=candidate,inputs=inputs,sources=sources,control_plan=plan,attack=attack)
            key='author-'+hashlib.sha256(canonical_json(document(call))).hexdigest()
            def execute():
                try:
                    try:result=author.author(selected.candidate,call=call)
                    except FactoryRecoveryRequired as pending:
                        if isinstance(pending,FactoryPublicationFailed):raise
                        try:result=author.recover(pending.claim)
                        except Exception as recovery:
                            pending.recovery_error=recovery
                            raise pending
                    return result,overhead()
                except (AuthoringBudgetExceeded,AuthoringBudgetUnverified) as exc:
                    return c.OperationResult(operation='construct',disposition=c.Disposition.REJECTED if isinstance(exc,AuthoringBudgetExceeded) else c.Disposition.BLOCKED,
                        artifacts=(),evidence=(),costs=overhead(),reason=str(exc)),overhead()
            last,_=self._step(claim,request_ref,key,{'candidate':document(selected.candidate),'call':document(call)},execute,child=True)
            if last.disposition!=c.Disposition.REJECTED or not last.artifacts:return last
            receipt=read_authoring_receipt(self.store,last.artifacts[-1])
            journal=json.loads(read_bytes(self.store,receipt.journal_refs[-1],65536))
            previous.append(str(journal.get('error') or last.reason)[:2048])
        return last

    def _execute(self,claim,ref,request):
        job,_,_=self._validated(claim)
        if job.state=='completed':return job.result
        for value in (ref,self.configuration):self.registry.assert_usable(value)
        selected,_=self._step(claim,ref,'intake',document(request),lambda:self._source(request))
        source,_=self._step(claim,ref,'source',{'candidate':document(selected.candidate)},
            lambda:(self._child(lambda:self.factory.screen_source(selected.candidate)),overhead()),child=True)
        if source.disposition!=c.Disposition.SUCCESS:return self._finish(claim,ref,source)
        prepared,_=self._step(claim,ref,'preparation',document(selected),lambda:self._prepare(selected))
        sources,resolver,baseline=self._sources(selected,prepared,request)
        author=self._author_factory(selected)
        profile=self.runtime.policy.profile
        allowed=c.AllowedChanges(source_roots=profile.source_roots,forbidden_paths=('.feature-rl','controller_checks','reference'),
            dependencies='pinned_allowlist',dependency_artifacts=(),additional_artifact_types=())
        profile.validate_allowed_changes(allowed)
        base_provenance=self._provenance(claim,sources)
        contract_inputs=ContractFinalizationInputs(visible_request=sources[0].text,
            allowed_requirement_ids=request.allowed_requirement_ids,entry_points=prepared.entry_points,
            supported_observables=prepared.supported_observables,runtime_discovery=prepared.context.source,
            allowed_changes=allowed,public_checks=request.public_checks,episode_limits=request.episode_limits,
            provenance_label=selected.provenance_label,visibility=c.Visibility.AUTHORING,provenance=base_provenance,costs=overhead())
        def identifiers(stage,index):
            prefix='feature-'+claim.job_id[:24]+'-'+stage+'-'+str(index)
            return dict(request_id=prefix,response_id=prefix+'-response',prompt_id=prefix+'-prompt',
                limits=self.settings.generation_limits,seed=request.seed_policy.seeds[0]+index)
        contract_result=self._author(claim,ref,author,selected,prepared,resolver,sources,contract_inputs,
            lambda index:build_contract_request(**identifiers('contract',index),sources=sources,
                allowed_requirement_ids=request.allowed_requirement_ids,entry_points=prepared.entry_points,
                supported_observables=prepared.supported_observables,allowed_changes=allowed))
        if contract_result.disposition!=c.Disposition.SUCCESS:return self._finish(claim,ref,contract_result)
        contract_ref=contract_result.artifacts[0];contract=typed(self.store,contract_ref,c.RequirementContract)
        scenario_inputs=ScenarioFinalizationInputs(contract=contract_ref,supported_observables=prepared.supported_observables,
            seed_policy=request.seed_policy,visibility=c.Visibility.PRIVATE,
            provenance=self._provenance(claim,sources,(contract_ref,)),costs=overhead())
        scenario_result=self._author(claim,ref,author,selected,prepared,resolver,sources,scenario_inputs,
            lambda index:build_scenario_request(**identifiers('scenario',index),contract=contract,contract_ref=contract_ref,sources=sources))
        if scenario_result.disposition!=c.Disposition.SUCCESS:return self._finish(claim,ref,scenario_result)
        scenario_ref=scenario_result.artifacts[0];scenario=typed(self.store,scenario_ref,c.ScenarioPlan)
        mandatory=tuple(sorted(r.requirement_id for r in contract.requirements+contract.compatibility_obligations if r.mandatory))
        feature=tuple(sorted(r.requirement_id for r in contract.requirements if r.mandatory))
        compatibility=tuple(sorted(r.requirement_id for r in contract.compatibility_obligations if r.mandatory))
        slots=[ControlSlot(category='omission',requirement_ids=(name,)) for name in mandatory]
        slots.extend(ControlSlot(category=category,requirement_ids=feature or mandatory) for category in ('plausible_wrong','hardcoded'))
        if compatibility:slots.append(ControlSlot(category='regression',requirement_ids=compatibility))
        slots.extend(ControlSlot(category='adversarial',requirement_ids=mandatory,attack=attack) for attack in sorted(ATTACKS))
        slots.append(ControlSlot(category='alternative_positive',requirement_ids=()))
        if len(slots)>self.settings.max_controls:raise ValueError('frozen workflow control count exceeds configured bound')
        plan=ControlPlan(contract=contract_ref,slots=tuple(slots));controls=[]
        evidence_resolver=AuthoringEvidenceResolver(store=self.store,**resolver.model_dump())
        for index,slot in enumerate(slots):
            alternative=slot.category=='alternative_positive'
            fixed=(selected.baseline,contract_ref,prepared.environment.recipe,*(() if alternative else (scenario_ref,)))
            control_inputs=ControlFinalizationInputs(control_id='control-'+str(index+1),category=slot.category,
                requirement_ids=slot.requirement_ids,expected_valid=alternative,
                expected_reason=('Implement the complete disclosed feature and preserve ordinary behavior from B only; independence and correctness remain unverified'
                    if alternative else 'Keep the program runnable while exposing '+(slot.attack or slot.category)+' for '+','.join(slot.requirement_ids)),
                baseline=selected.baseline,contract=contract_ref,environment=prepared.environment.recipe,
                scenario_plan=None if alternative else scenario_ref,
                provenance=self._provenance(claim,sources,fixed),costs=overhead())
            result=self._author(claim,ref,author,selected,prepared,resolver,sources,control_inputs,
                lambda attempt:build_control_request(**identifiers('control-'+str(index+1),attempt),store=self.store,
                    resolver=evidence_resolver,inputs=control_inputs,sources=sources),plan=plan,attack=slot.attack)
            if result.disposition!=c.Disposition.SUCCESS:return self._finish(claim,ref,result)
            controls.append(read_record(self.store,result.artifacts[0],ControlRecord,'m4-control-record').control)
        checker_inputs=CheckerFinalizationInputs(contract=contract_ref,scenario_plan=scenario_ref,baseline=selected.baseline,
            environment=prepared.environment.recipe,output_limit_bytes=self.runtime.policy.output_bytes,
            public_examples=request.public_checks,controls=tuple(controls),visibility=c.Visibility.PRIVATE,
            provenance=self._provenance(claim,sources,(contract_ref,scenario_ref,selected.baseline,prepared.environment.recipe)),costs=overhead())
        checker_result=self._author(claim,ref,author,selected,prepared,resolver,sources,checker_inputs,
            lambda index:build_checker_request(**identifiers('checker',index),contract=contract,contract_ref=contract_ref,
                plan=scenario,plan_ref=scenario_ref,sources=sources))
        if checker_result.disposition!=c.Disposition.SUCCESS:return self._finish(claim,ref,checker_result)
        inputs=BuildInputs(source_pair=selected.source_pair,contract=contract_ref,scenario_plan=scenario_ref,
            verifier=checker_result.artifacts[0],environment=prepared.environment,
            baseline_files=tuple(sorted(baseline.files)),invocation=request.invocation)
        result,_=self._step(claim,ref,'construction',{'candidate':document(selected.candidate),'inputs':document(inputs)},
            lambda:(self._child(lambda:author.construct(selected.candidate,inputs=inputs)),overhead()),child=True)
        return self._finish(claim,ref,result)

    def _finish(self,claim,ref,result):
        current=self._observation(claim,'completion')
        if current is not None:
            refs=[r for r in current.observation.receipts if r.kind=='m6-feature-outcome']
            if len(refs)!=1:raise ValueError('workflow completion lacks its exact frozen outcome')
            return self._publish_outcome(read_bytes(self.store,refs[0],MAX_DOCUMENT),claim)
        steps=tuple(r for row in self.registry.accounting(claim.job_id).observations
            if row.attempt_id==claim.attempt_id for r in row.observation.receipts if r.kind=='m6-feature-step')
        outcome=FeatureOutcome(claim=claim,request=ref,selected=result,steps=tuple(dict.fromkeys(steps)),
            recorded_at=self._claimed_at(claim),limitations=(
                'BUILT artifacts are not qualification, human approval, independence evidence or released tasks.',
                'Negative controls use B and the frozen contract/scenarios; reference behavior is never supplied.',
                'Monetary costs and unmeasured controller overhead remain unknown; child costs remain in their original Registry jobs.'))
        return self._publish_outcome(canonical_json(document(outcome)),claim)

    def _publish_outcome(self,payload,claim):
        if len(payload)>MAX_DOCUMENT:raise ValueError('workflow result exceeds publication bound')
        outcome=FeatureOutcome.model_validate_json(payload);job,ref,_=self._validated(claim)
        if outcome.claim!=claim or outcome.request!=ref or canonical_json(document(outcome))!=payload:
            raise ValueError('workflow result changed selected request/claim')
        if job.state=='completed':return job.result
        rows=[row for row in self.registry.accounting(claim.job_id).observations if row.attempt_id==claim.attempt_id]
        selected={r for row in rows for r in row.observation.receipts if r.kind=='m6-feature-step'}
        if set(outcome.steps)!=selected:raise ValueError('workflow result omits or substitutes selected phases')
        values=[read_record(self.store,r,FeatureStep,'m6-feature-step') for r in outcome.steps]
        if not any(value.output==outcome.selected and (value.key=='construction'
                or outcome.selected.disposition!=c.Disposition.SUCCESS) for value in values):
            raise ValueError('workflow result lacks its selected terminal child output')
        for row in rows:
            if row.observation.source!='m6-feature-completion' and len([
                    r for r in row.observation.receipts if r.kind=='m6-feature-step'])!=1:
                raise ValueError('workflow cannot complete with an unresolved phase')
        for value in values:self._check_step(value)
        try:
            output=put(self.factory,outcome,'m6-feature-outcome',dependencies=references(document(outcome)))
            current=self._observation(claim,'completion')
            if current is not None:
                refs=[r for r in current.observation.receipts if r.kind=='m6-feature-outcome']
                if refs!=[output]:raise ValueError('workflow completion changed its frozen bytes')
            else:self._observe(claim,'completion',(output,),overhead())
            rows=[row for row in self.registry.accounting(claim.job_id).observations if row.attempt_id==claim.attempt_id]
            evidence=c.EvidenceRecord(producer='feature_rl.pipeline.FeatureWorkflow',
                command=('Factory.construct_feature',ref.sha256),recorded_at=outcome.recorded_at,
                exit_status=0 if outcome.selected.disposition==c.Disposition.SUCCESS else 1,
                artifacts=(output,),revision=self.factory.revision,scope='source_inspection')
            result=c.OperationResult(operation='construct',disposition=outcome.selected.disposition,
                artifacts=(*outcome.selected.artifacts,output),evidence=(*outcome.selected.evidence,evidence),
                costs=collapse_costs(tuple(cost for row in rows for cost in row.observation.costs)),reason=outcome.selected.reason)
            return self.registry.complete(claim,result,observations=tuple(row.observation_id for row in rows)).result
        except Exception as exc:
            raise FactoryPublicationFailed('retain exact automatic construction result',claim,payload,kind='m6-feature-outcome') from exc

    def retry_publication(self,pending):
        if pending.kind=='m6-feature-outcome':return self._publish_outcome(pending.payload,pending.claim)
        if pending.kind!='m6-feature-step':raise ValueError('unsupported automatic workflow publication')
        self._publish_step(pending.payload,pending.claim)
        return self.recover(pending.claim)
