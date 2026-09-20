"""Thin argparse commands over real M0/M3/M5/M6 services."""
import argparse
import base64
from dataclasses import fields, is_dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import sys

from pydantic import BaseModel
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import BuildInputs
from feature_rl.pipeline.configuration import CLIConfiguration, ConfigurationRequired, compose
from feature_rl.qualification import QualificationPolicy
from feature_rl.qualification.evidence import unknown_cost

MAX_INPUT=1024*1024


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def read_json(path,model):
    """Bound regular local JSON inputs; no YAML, pickle, executable config or FIFO."""
    fd=os.open(Path(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_INPUT:
            raise ValueError('configuration/request must be a bounded regular JSON file')
        raw=stream.read(MAX_INPUT+1)
    if len(raw)>MAX_INPUT:raise ValueError('JSON input byte limit exceeded')
    value=json.loads(raw,object_pairs_hook=_pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
    return model.model_validate_json(canonical_json(value))


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
    if isinstance(value,(tuple,list)):return [_retained(item) for item in value]
    if isinstance(value,dict):return {key:_retained(item) for key,item in value.items()}
    if is_dataclass(value):
        state={field.name:_retained(getattr(value,field.name)) for field in fields(value)}
    elif isinstance(value,Exception):state={key:_retained(item) for key,item in vars(value).items()}
    else:
        from enum import Enum
        if isinstance(value,Enum):return value.value
        raise TypeError('unsupported retained recovery value: '+type(value).__name__)
    return {'type':type(value).__module__+'.'+type(value).__name__,'state':state}


def _emit(value,stream=None):
    # Resolve stdout at call time so library callers may capture command output.
    target=sys.stdout if stream is None else stream
    target.write(canonical_json(value).decode('utf-8')+'\n');target.flush()


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command=='config-schema':
        _emit(CLIConfiguration.model_json_schema(),None);return 0
    operation={'screen-source':'construct','accept':'qualify','resolve':'release'}.get(args.command,args.command)
    try:
        if args.config is None:raise ConfigurationRequired('--config is required for actual operations')
        config=read_json(args.config,CLIConfiguration)
        # Validate all supplied request bytes before any state or runtime is opened.
        inputs=policy=report=attestation=None
        if args.command in ('screen-source','construct'):
            request=read_json(args.request,c.ConstructRequest)
            if args.command=='construct' and args.inputs:inputs=read_json(args.inputs,BuildInputs)
        elif args.command=='accept':
            request=read_json(args.review_request,c.ArtifactRef)
            attestation=read_json(args.attestation,c.ArtifactRef)
        else:
            model={'qualify':c.QualifyRequest,'release':c.ReleaseRequest,'resolve':c.TaskRequest}[args.command]
            request=read_json(args.request,model)
            if args.command=='qualify' and args.policy:policy=read_json(args.policy,QualificationPolicy)
            if args.command=='release' and args.accepted_report:report=read_json(args.accepted_report,c.ArtifactRef)
        app=compose(config,qualification=args.command in ('qualify','accept','release','resolve'))
        factory=app.factory
        if args.command=='screen-source':result=factory.screen_source(request.candidate)
        elif args.command=='construct':result=factory.construct(request.candidate,inputs=inputs)
        elif args.command=='qualify':result=factory.qualify(request.task_version,policy=policy)
        elif args.command=='accept':result=factory.accept(request,attestation)
        elif args.command=='release':result=factory.release(request.task_version,accepted_report=report)
        else:
            task=app.resolver.resolve_released(request.task_version)
            _emit(task.model_dump(mode='json'),None);return 0
        _emit(result.model_dump(mode='json'),None)
        return 0 if result.disposition==c.Disposition.SUCCESS else 1
    except Exception as exc:
        # Preserve actual pending payloads/costs/claims on controller stderr before
        # this process exits. Never redispatch work to turn an unknown into zero.
        if any(hasattr(exc,name) for name in ('claim','pending','payload','upstream','receipt')):
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
        result=c.OperationResult(operation=operation,disposition=disposition,artifacts=(),evidence=(),
            costs=(unknown_cost('construction','CLI/controller prerequisite or failure overhead unmeasured; any upstream attempt remains in Registry or the private retained recovery receipt'),),
            reason=(type(exc).__name__+': '+str(exc))[:4096])
        _emit(result.model_dump(mode='json'),None);return 2
