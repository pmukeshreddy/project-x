"""Thin argparse commands over the actual artifact, Factory and M7/M8 services."""
import argparse
import base64
from dataclasses import fields, is_dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import sys
from typing import Annotated,Literal

from pydantic import BaseModel,Field,TypeAdapter
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import BuildInputs,AuthoringCall
from feature_rl.pipeline.configuration import CLIConfiguration, ConfigurationRequired, compose
from feature_rl.qualification import QualificationPolicy
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.registry import Claim

MAX_INPUT=1024*1024
SERVICES=('factory','grade','qualification','lifecycle','run','evaluate','audit')


class RetainedClaim(c.StrictModel):
    model: Literal['feature_rl.registry.models.Claim']
    value: Claim


class RetainedBytes(c.StrictModel):
    bytes_base64: Annotated[str,Field(max_length=1400000)]


class RetainedFactoryState(c.StrictModel):
    claim: RetainedClaim
    payload: RetainedBytes
    sha256: c.Digest
    kind: Literal['m6-source-disposition','m6-construction-result','m6-authoring-receipt',
        'm6-frozen-grade','m6-pending-grade-result']


class RetainedFactoryError(c.StrictModel):
    type: Literal['feature_rl.pipeline.factory.FactoryPublicationFailed']
    state: RetainedFactoryState
    message: str | None=None


class RetainedFactoryReceipt(c.StrictModel):
    version: Literal['m6-cli-retained-recovery-v1']
    recovery: RetainedFactoryError


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def read_json(path,model,*,max_bytes=MAX_INPUT):
    """Bound regular local JSON inputs; no YAML, pickle, executable config or FIFO."""
    fd=os.open(Path(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:
            raise ValueError('configuration/request must be a bounded regular JSON file')
        raw=stream.read(max_bytes+1)
    if len(raw)>max_bytes:raise ValueError('JSON input byte limit exceeded')
    value=json.loads(raw,object_pairs_hook=_pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
    payload=canonical_json(value)
    return model.validate_json(payload) if isinstance(model,TypeAdapter) else model.model_validate_json(payload)


def parser():
    root=argparse.ArgumentParser(prog='feature-rl',description='Immutable artifact Factory and current admission commands.')
    root.add_argument('--config',help='strict local CLIConfiguration JSON; config-schema prints its schema')
    commands=root.add_subparsers(dest='command',required=True)
    commands.add_parser('config-schema',help='print the strict composition schema without opening state or a runtime')
    for name,help_text in (
        ('screen-source','retain actual CandidateRecord screening/license outcome and original costs'),
        ('construct','build from actual supplied BuildInputs; missing inputs remain a selected blocked outcome'),
        ('qualify','execute actual M5 gates on complete BUILT T0; requires configured M3/M5'),
        ('accept','consume external human attestation through actual M5; requires configured M3/M5'),
        ('release','apply exact accepted qualification and both legal lifecycle transitions'),
        ('resolve','verify current released-task admission through the actual selected chain')):
        command=commands.add_parser(name,help=help_text)
        if name=='accept':
            command.add_argument('--review-request',required=True,help='JSON ArtifactRef of selected M5 review request')
            command.add_argument('--attestation',required=True,help='JSON ArtifactRef of actual external detached attestation')
        else:
            command.add_argument('--request',required=True,help='JSON M0 ConstructRequest or task request')
        if name=='construct':command.add_argument('--inputs',help='complete M6 BuildInputs JSON; no automatic generation')
        if name=='qualify':command.add_argument('--policy',help='actual QualificationPolicy JSON; selected Factory history is enforced')
        if name=='release':command.add_argument('--accepted-report',help='JSON ArtifactRef of accepted Q when request is BUILT')
    for name in ('author','import-authoring'):
        command=commands.add_parser(name,help='actual selected M2/M4 call' if name=='author' else 'inert import of retained rejected M2 journals; no generation')
        command.add_argument('--request',required=True,help='M0 ConstructRequest JSON')
        command.add_argument('--call',required=True,help='actual M6 AuthoringCall JSON')
        if name=='import-authoring':command.add_argument('--journals',required=True,help='ordered JSON array of actual retained GenerationJournal ArtifactRefs')
    for name,model in (('grade','GradeRequest'),('run','RunRequest'),('train','TrainRequest'),('evaluate','EvaluateRequest')):
        command=commands.add_parser(name,help='delegate to the actual '+name+' service')
        command.add_argument('--request',required=True,help='M0 '+model+' JSON')
        if name in ('run','train'):command.add_argument('--invocation',required=True,help='stable selected job invocation; reuse for ordinary recovery')
        if name=='grade':command.add_argument('--invocation',default='grade',help='stable grade invocation; new value requests a distinct cost-bearing grade')
        if name=='train':
            command.add_argument('--resume',help='exact selected TrainingCheckpoint ArtifactRef JSON')
            command.add_argument('--demonstrations',help='JSON array of actual M7 source/trajectory demonstrations; SFT only')
    audit=commands.add_parser('audit',help='actual frozen M8 selection plus external attestations; no generated human approval')
    selection=audit.add_mutually_exclusive_group(required=True)
    selection.add_argument('--request',help='M0 AuditRequest JSON with the selected patch run IDs')
    selection.add_argument('--source-only',action='store_true',help='audit a frozen source-only selection with no rollout IDs')
    for name in ('recover','retry-publication'):
        command=commands.add_parser(name,help='recover selected service state without implicit redispatch of unknown work')
        command.add_argument('--service',choices=SERVICES,default='factory',help='actual service composition for the retained job')
        command.add_argument('--claim' if name=='recover' else '--receipt',required=True,
            help='actual Registry Claim JSON' if name=='recover' else 'one retained FactoryPublicationFailed stderr JSON record; no object loader')
    return root


def _retained(value):
    """Lossless ordinary JSON receipt for private, in-memory recovery capabilities.

    This is output only, never an executable object loader. Concrete service retry
    methods remain the authority for validating and consuming retained capabilities.
    """
    if value is None or type(value) in (str,int,float,bool):return value
    if isinstance(value,BaseModel):return {'model':type(value).__module__+'.'+type(value).__name__,
        'value':value.model_dump(mode='json')}
    if type(value) is bytes:return {'bytes_base64':base64.b64encode(value).decode('ascii')}
    if isinstance(value,datetime):return {'datetime_iso8601':value.isoformat()}
    if isinstance(value,Path):return {'path':str(value)}
    if isinstance(value,(tuple,list)):return [_retained(item) for item in value]
    if isinstance(value,dict):return {key:_retained(item) for key,item in value.items()}
    if is_dataclass(value):
        state={field.name:_retained(getattr(value,field.name)) for field in fields(value)}
    elif isinstance(value,BaseException):
        state={key:_retained(item) for key,item in vars(value).items()}
        if isinstance(value,BaseExceptionGroup):state['exceptions']=[_retained(item) for item in value.exceptions]
        return {'type':type(value).__module__+'.'+type(value).__name__,'message':str(value),'state':state}
    else:
        from enum import Enum
        if isinstance(value,Enum):return value.value
        raise TypeError('unsupported retained recovery value: '+type(value).__name__)
    return {'type':type(value).__module__+'.'+type(value).__name__,'state':state}


def _emit(value,stream=None):
    # Resolve stdout at call time so library callers may capture command output.
    target=sys.stdout if stream is None else stream
    target.write(canonical_json(value).decode('utf-8')+'\n');target.flush()


def _with_cleanup(app,action):
    failure=None;result=None
    try:
        result=action();return result
    except BaseException as exc:
        failure=exc
        # Keep selected native identities even when an upstream OS/CUDA error
        # carries no custom recovery fields. This reads the actual services only.
        selected=[]
        for service in (app.factory.native_run,app.factory.training,app.factory.evaluation):
            if service is None:continue
            claim=getattr(service,'last_claim',None) or getattr(service,'claim',None)
            if type(claim) is Claim:
                selected.append({'claim':claim,'journal':getattr(service,'journal',None)})
        if selected:exc.cli_selected_jobs=selected
        raise
    finally:
        try:app.close()
        except BaseException as cleanup:
            if failure is None:
                cleanup.cli_completed_result=result
                raise
            failure.cli_cleanup_error=cleanup


def _composition(args):
    service=args.service if args.command in ('recover','retry-publication') else args.command
    return {'runtime':service=='grade','qualification':service in ('qualify','accept','release','resolve','qualification','lifecycle'),
        'authoring':args.command in ('author','import-authoring'),
        'native_operation':service if service in ('run','train','evaluate') else None,'audit':service=='audit'}


def _factory_receipt(value):
    from feature_rl.pipeline import FactoryPublicationFailed
    retained=value.recovery.state
    payload=base64.b64decode(retained.payload.bytes_base64,validate=True)
    pending=FactoryPublicationFailed('retained CLI publication',retained.claim.value,payload,kind=retained.kind)
    if pending.sha256!=retained.sha256:raise ValueError('retained Factory publication digest changed')
    return pending


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command=='config-schema':
        _emit(CLIConfiguration.model_json_schema(),None);return 0
    operation={'screen-source':'construct','author':'construct','import-authoring':'construct',
        'accept':'qualify','resolve':'release','recover':'construct','retry-publication':'construct'}.get(args.command,args.command)
    try:
        if args.config is None:raise ConfigurationRequired('--config is required for actual operations')
        config=read_json(args.config,CLIConfiguration)
        # Validate all supplied request bytes before any state or runtime is opened.
        inputs=policy=report=attestation=call=resume=None;journals=demonstrations=()
        if args.command in ('screen-source','construct','author','import-authoring'):
            request=read_json(args.request,c.ConstructRequest)
            if args.command=='construct' and args.inputs:inputs=read_json(args.inputs,BuildInputs)
            if args.command in ('author','import-authoring'):call=read_json(args.call,AuthoringCall)
            if args.command=='import-authoring':journals=read_json(args.journals,TypeAdapter(tuple[c.ArtifactRef,...]))
        elif args.command=='accept':
            request=read_json(args.review_request,c.ArtifactRef)
            attestation=read_json(args.attestation,c.ArtifactRef)
        elif args.command=='audit':
            request=() if args.source_only else read_json(args.request,c.AuditRequest).run_ids
        elif args.command=='recover':
            from feature_rl.registry import Claim
            request=read_json(args.claim,Claim)
        elif args.command=='retry-publication':
            request=_factory_receipt(read_json(args.receipt,RetainedFactoryReceipt,max_bytes=2*MAX_INPUT))
        else:
            model={'qualify':c.QualifyRequest,'release':c.ReleaseRequest,'resolve':c.TaskRequest,
                'grade':c.GradeRequest,'run':c.RunRequest,'train':c.TrainRequest,'evaluate':c.EvaluateRequest}[args.command]
            request=read_json(args.request,model)
            if args.command=='qualify' and args.policy:policy=read_json(args.policy,QualificationPolicy)
            if args.command=='release' and args.accepted_report:report=read_json(args.accepted_report,c.ArtifactRef)
            if args.command=='train':
                from feature_rl.training.service import Demonstration
                if args.resume:resume=read_json(args.resume,c.ArtifactRef)
                if args.demonstrations:demonstrations=read_json(args.demonstrations,TypeAdapter(tuple[Demonstration,...]))
        app=compose(config,**_composition(args))
        factory=app.factory
        if args.command in ('recover','retry-publication'):
            claim=request if args.command=='recover' else request.claim
            operation=factory.registry.job(claim.job_id).spec.operation
        def dispatch():
            if args.command=='screen-source':return factory.screen_source(request.candidate)
            if args.command=='construct':return factory.construct(request.candidate,inputs=inputs)
            if args.command=='author':return factory.author(request.candidate,call=call)
            if args.command=='import-authoring':return factory.import_rejected_authoring(request.candidate,call=call,journal_refs=journals)
            if args.command=='qualify':return factory.qualify(request.task_version,policy=policy)
            if args.command=='accept':return factory.accept(request,attestation)
            if args.command=='release':return factory.release(request.task_version,accepted_report=report)
            if args.command=='resolve':return app.resolver.resolve_released(request.task_version)
            if args.command=='grade':return factory.grade(request.task_version,request.submission,request.case_seed,invocation=args.invocation)
            if args.command=='run':return factory.run(request.task_version,request.policy,request.limits,case_seed=request.case_seed,invocation=args.invocation)
            if args.command=='train':return factory.train(request.config,invocation=args.invocation,resume=resume,demonstrations=demonstrations)
            if args.command=='evaluate':return factory.evaluate(request.config)
            if args.command=='audit':return factory.audit(request)
            if args.command=='recover':return factory.recover(request)
            return factory.retry_publication(request)
        result=_with_cleanup(app,dispatch)
        _emit(result.model_dump(mode='json'),None)
        if args.command=='resolve':return 0
        return 0 if result.disposition==c.Disposition.SUCCESS else 1
    except Exception as exc:
        # Preserve actual pending payloads/costs/claims on controller stderr before
        # this process exits. Never redispatch work to turn an unknown into zero.
        if any(hasattr(exc,name) for name in ('claim','pending','payload','upstream','receipt','job_id','journal',
                'cli_cleanup_error','cli_completed_result','cli_selected_jobs','native_cleanup_error')):
            _emit({'version':'m6-cli-retained-recovery-v1','recovery':_retained(exc)},sys.stderr)
        disposition=c.Disposition.BLOCKED if isinstance(exc,ConfigurationRequired) else c.Disposition.INFRASTRUCTURE
        if isinstance(exc,ValueError) and not isinstance(exc,ConfigurationRequired):disposition=c.Disposition.INVALID
        from feature_rl.pipeline import AdmissionRejected
        from feature_rl.qualification import QualificationRejected
        from feature_rl.qualification.controls import disposition_for
        from feature_rl.registry import QuarantinedError
        if isinstance(exc,AdmissionRejected):disposition=c.Disposition.PROVISIONAL
        if isinstance(exc,QualificationRejected):disposition=disposition_for((exc.code,))
        if isinstance(exc,QuarantinedError):disposition=c.Disposition.BLOCKED
        from feature_rl.pipeline import AuthoringBudgetExceeded,AuthoringBudgetUnverified
        if isinstance(exc,AuthoringBudgetExceeded):disposition=c.Disposition.REJECTED
        if isinstance(exc,AuthoringBudgetUnverified):disposition=c.Disposition.BLOCKED
        result=c.OperationResult(operation=operation,disposition=disposition,artifacts=(),evidence=(),
            costs=(unknown_cost('construction','CLI/controller prerequisite or failure overhead unmeasured; any upstream attempt remains in Registry or the private retained recovery receipt'),),
            reason=(type(exc).__name__+': '+str(exc))[:4096])
        _emit(result.model_dump(mode='json'),None);return 2
