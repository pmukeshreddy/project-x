"""Append-only proof of one specific controller transport accounting correction."""
import base64
import hashlib
from typing import Literal

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.generation.provider import GenerationProviderError
from feature_rl.generation.schema import codex_request_schema, _json_no_duplicates
from feature_rl.requirements.service import archive_read_caps
from feature_rl.verifiers import CheckerFinalizationInputs, CheckerProposal
from feature_rl.verifiers.fragments import CheckerFragmentInputs, CheckerFragmentProposal
from .packaging import document, read_record


PROOF_KIND = 'm6-transport-repair-classification'


class TransportRepairProof(c.StrictModel):
    version: Literal['m6-unsafe-integer-transport-v1'] = 'm6-unsafe-integer-transport-v1'
    candidate: c.ArtifactRef
    job_id: c.Digest
    request: c.ArtifactRef
    receipt: c.ArtifactRef
    policy: c.ArtifactRef
    archives: tuple[c.ArtifactRef, ...]
    original_transport_sha256: c.Digest
    corrected_transport_sha256: c.Digest


def remove_unsafe_integer_bounds(value):
    """Remove only the diagnosed remote numeric constraints, not other dialect changes."""
    if isinstance(value, list):
        return [remove_unsafe_integer_bounds(item) for item in value]
    if not isinstance(value, dict):return value
    return {key: remove_unsafe_integer_bounds(item) for key, item in value.items()
        if not (value.get('type') == 'integer'
            and key in {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'}
            and type(item) in {int, float} and abs(item) > 2**53-1)}


def transport_failure_proof(factory, job, request):
    """Recompute exact proof from completed, selected provider archives; no inference."""
    from . import authoring as a
    schemas={CheckerFinalizationInputs:CheckerProposal, CheckerFragmentInputs:CheckerFragmentProposal}
    schema=schemas.get(type(request.call.inputs))
    if (schema is None or request.origin!='factory_dispatch' or job.state!='completed'
            or job.result is None or job.result.disposition!=c.Disposition.REJECTED):return None
    if (len(job.spec.inputs)!=2 or job.spec.inputs!=(request.candidate, job.spec.inputs[1])
            or job.spec.invocation!='m6-author:'+request.lane
            or read_record(factory.store,job.spec.inputs[1],a.AuthoringRequest,'m6-authoring-request')!=request):
        raise ValueError('transport classification changed its exact authoring job')
    historical,receipt=a.completed_receipt(factory,job,request)
    outcome=a.archived_outcome(historical,request,receipt.claim,schema)
    if (not isinstance(outcome,GenerationProviderError) or outcome.record.error_code!='CodexUnavailable'
            or outcome.record.generation_succeeded or not outcome.record.publication_complete
            or outcome.response is None or outcome.response.get('output_text') is not None
            or outcome.response.get('termination')!='process_exit' or receipt.outputs):return None
    old=request.call.generation.request
    cap,envelope=archive_read_caps('events',old)
    raw=factory.store.get_bytes(outcome.record.archives['events'],max_payload_bytes=cap,max_envelope_bytes=envelope)
    if base64.b64decode(outcome.response.get('stdout_base64',''),validate=True)!=raw:
        raise ValueError('transport classification events differ from provider response')
    events=[_json_no_duplicates(line) for line in raw.decode().splitlines()]
    if (not events or any(not isinstance(event,dict) for event in events)
            or any(event.get('type')=='turn.completed' or
                (isinstance(event.get('item'),dict) and event['item'].get('type')=='agent_message') for event in events)):
        return None
    terminal=events[-1];error=terminal.get('error')
    if (terminal.get('type')!='turn.failed' or not isinstance(error,dict)
            or not isinstance(error.get('message'),str)
            or not error['message'].endswith('Incomplete response returned, reason: max_output_tokens')):return None
    original=outcome.response.get('transport_schema')
    if not isinstance(original,dict):return None
    corrected=remove_unsafe_integer_bounds(original)
    if corrected==original or corrected!=codex_request_schema(old,schema):return None
    digest=lambda value:hashlib.sha256(canonical_json(value)).hexdigest()
    return TransportRepairProof(candidate=request.candidate,job_id=job.job_id,
        request=job.spec.inputs[1],receipt=job.result.artifacts[-1],policy=job.spec.configuration,
        archives=tuple(outcome.record.archives[name] for name in sorted(outcome.record.archives)),
        original_transport_sha256=digest(original),corrected_transport_sha256=digest(corrected))


def classifications(factory, jobs):
    """Publish deterministic additional evidence; leave every original repair flag intact."""
    from .construction import put, references
    result={}
    for job,request in jobs:
        if not request.repair:continue
        proof=transport_failure_proof(factory,job,request)
        if proof is not None:
            result[job.spec.inputs[1]]=put(factory,proof,PROOF_KIND,dependencies=references(document(proof)))
    return result


def authenticated_exclusions(history, *, store, registry):
    """M5 reauthenticates evidence instead of trusting an asserted classification."""
    from types import SimpleNamespace
    from . import authoring as a
    refs=tuple(ref for ref in history.journal_refs if ref.kind==PROOF_KIND)
    if not refs:return set()
    if store is None or registry is None or registry.store.root!=store.root:
        raise ValueError('transport classification requires store/Registry authentication context')
    if len(set(refs))!=len(refs):raise ValueError('duplicate transport classification proofs')
    exclusions=set()
    factory=SimpleNamespace(store=store,registry=registry)
    for ref in refs:
        registry.assert_usable(ref)
        proof=read_record(store,ref,TransportRepairProof,PROOF_KIND)
        if proof.candidate!=history.candidate:raise ValueError('transport classification candidate mismatch')
        job=registry.job(proof.job_id)
        request=read_record(store,proof.request,a.AuthoringRequest,'m6-authoring-request')
        if (len(job.spec.inputs)!=2 or not request.repair or proof.request!=job.spec.inputs[1]
                or transport_failure_proof(factory,job,request)!=proof):
            raise ValueError('transport classification proof differs from authenticated attempt')
        matches=[attempt for attempt in history.attempts if attempt.after==proof.receipt]
        if len(matches)!=1 or proof.receipt in exclusions:
            raise ValueError('transport classification must select exactly one retained physical repair')
        attempt=matches[0];receipt=a.read_authoring_receipt(store,proof.receipt)
        if (attempt.stage!=request.stage or attempt.diagnosis!=request.call.generation.diagnosis
                or attempt.change!=request.call.generation.changed_input or attempt.costs!=receipt.costs
                or attempt.evidence!=job.result.evidence):
            raise ValueError('transport classification changed original repair evidence/costs')
        if not {proof.request,proof.receipt,*receipt.journal_refs}<=set(history.journal_refs):
            raise ValueError('transport classification omits original physical history')
        exclusions.add(proof.receipt)
    return exclusions
