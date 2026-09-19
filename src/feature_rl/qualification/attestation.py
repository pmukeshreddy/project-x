"""SSHSIG verification plus an external, OS-protected human enrollment boundary.

There is deliberately no key-generation, signer-enrollment or signing API.
A model-controlled allowlist is sufficient for a cryptographic diagnostic only.
"""
from datetime import datetime, timezone
import base64
import hashlib
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import tempfile
import time
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import StrictModel, Digest, UTCDateTime
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_bytes, read_local
from .models import QualificationRejected, ReviewPayload, ReviewRequest, DetachedAttestation

BINARY=Path('/usr/bin/ssh-keygen')
BINARY_SHA256='6949a5fb9e80c47f2126e523db7b232b37794fb33da9f70b2dd8b8742802248e'
NAMESPACE='feature-rl-human-review-v1'
ENV={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC'}
Identity=Annotated[str,Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$')]


def protected_bytes(path,cap):
    """Open only root-owned paths outside the controller/model write authority."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise QualificationRejected('unverified_human_review','externally administered absolute trust path required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for index,part in enumerate(path.parts[1:]):
            final=index==len(path.parts)-2
            nxt=os.open(part,os.O_RDONLY|os.O_NOFOLLOW|(0 if final else os.O_DIRECTORY),dir_fd=fd)
            os.close(fd);fd=nxt
            info=os.fstat(fd)
            if info.st_uid!=0 or info.st_mode&0o022 or (not final and not stat.S_ISDIR(info.st_mode)):
                raise QualificationRejected('unverified_human_review','externally administered root-owned non-writable trust path required')
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>cap:
            raise QualificationRejected('unverified_human_review','external trust file type/size rejected')
        chunks=[];remaining=cap+1
        while remaining:
            chunk=os.read(fd,min(65536,remaining))
            if not chunk:break
            chunks.append(chunk);remaining-=len(chunk)
        data=b''.join(chunks)
        if len(data)>cap:raise QualificationRejected('unverified_human_review','external trust file grew beyond cap')
        return data
    except OSError as exc:
        raise QualificationRejected('unverified_human_review','externally administered trust file unavailable or unsafe: '+type(exc).__name__) from exc
    finally:os.close(fd)


def _verify_process(argv,payload,timeout=5.0,cap=65536):
    process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env=ENV,start_new_session=True,close_fds=True)
    outputs={'stdout':bytearray(),'stderr':bytearray()};sent=0;deadline=time.monotonic()+timeout
    selector=selectors.DefaultSelector()
    try:
        for stream,events,label in ((process.stdin,selectors.EVENT_WRITE,'stdin'),(process.stdout,selectors.EVENT_READ,'stdout'),(process.stderr,selectors.EVENT_READ,'stderr')):
            os.set_blocking(stream.fileno(),False);selector.register(stream,events,label)
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise QualificationRejected('unverified_human_review','SSHSIG verification deadline exceeded')
            for key,_ in selector.select(min(remaining,0.1)):
                stream=key.fileobj;label=key.data
                if label=='stdin':
                    if sent==len(payload):selector.unregister(stream);stream.close();continue
                    try:sent+=os.write(stream.fileno(),payload[sent:sent+8192])
                    except BrokenPipeError:selector.unregister(stream);stream.close()
                else:
                    try:part=os.read(stream.fileno(),8192)
                    except BlockingIOError:continue
                    if not part:selector.unregister(stream);stream.close();continue
                    if sum(len(value) for value in outputs.values())+len(part)>cap:
                        raise QualificationRejected('unverified_human_review','SSHSIG verification output cap exceeded')
                    outputs[label].extend(part)
        status=process.wait(timeout=max(0.001,deadline-time.monotonic()))
        return status,bytes(outputs['stdout']),bytes(outputs['stderr'])
    finally:
        selector.close()
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=2)
        for stream in (process.stdin,process.stdout,process.stderr):
            if not stream.closed:stream.close()


def verify_sshsig(payload,signature,allowed_signers,principal,namespace,*,binary=BINARY,binary_sha256=BINARY_SHA256):
    """Cryptographic mechanism only. This never establishes human origin."""
    if Path(binary)!=BINARY:raise QualificationRejected('unverified_human_review','only the absolute system SSHSIG verifier is supported')
    if any(type(value) is not bytes for value in (payload,signature,allowed_signers)) or len(payload)>65536 or not 1<=len(signature)<=16384 or len(allowed_signers)>65536:
        raise QualificationRejected('unverified_human_review','bounded signature/message/allowlist bytes required')
    if not principal or len(principal)>128 or not namespace or len(namespace)>128 or any(ord(ch)<32 for ch in principal+namespace):
        raise QualificationRejected('unverified_human_review','invalid signer identity/namespace')
    executable=protected_bytes(binary,8*1024*1024)
    if hashlib.sha256(executable).hexdigest()!=binary_sha256:raise QualificationRejected('unverified_human_review','system SSHSIG verifier digest mismatch')
    with tempfile.TemporaryDirectory(prefix='feature-rl-m5-verify-') as temporary:
        directory=Path(temporary);allowed=directory/'allowed-signers';sig=directory/'signature'
        allowed.write_bytes(allowed_signers);sig.write_bytes(signature)
        argv=[str(binary),'-Y','verify','-f',str(allowed),'-I',principal,'-n',namespace,'-s',str(sig)]
        status,stdout,stderr=_verify_process(argv,payload)
    result={'version':'m5-signature-check-v1','argv':argv,'exit_status':status,'stdout_utf8':stdout.decode('utf-8','replace'),
        'stderr_utf8':stderr.decode('utf-8','replace'),'stdout_sha256':hashlib.sha256(stdout).hexdigest(),
        'stderr_sha256':hashlib.sha256(stderr).hexdigest(),'payload_sha256':hashlib.sha256(payload).hexdigest(),
        'signature_sha256':hashlib.sha256(signature).hexdigest(),'allowed_signers_sha256':hashlib.sha256(allowed_signers).hexdigest(),
        'binary_sha256':binary_sha256,'verified_at':datetime.now(timezone.utc).isoformat(),'human_origin_verified':False}
    if status!=0:
        error=QualificationRejected('unverified_human_review','SSHSIG signature/identity/namespace verification failed')
        error.verification=result
        raise error
    return result


class EnrolledReviewer(StrictModel):
    human_identity: Identity
    public_key: Annotated[str,Field(pattern=r'^ssh-ed25519 [A-Za-z0-9+/]+={0,2}$',max_length=256)]
    fingerprint: Annotated[str,Field(pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')]
    enrollment_evidence: Annotated[str,Field(min_length=1,max_length=4096)]

class HumanEnrollment(StrictModel):
    version: Literal['m5-external-human-enrollment-v1']='m5-external-human-enrollment-v1'
    origin: Literal['externally-administered-human-reviewer-enrollment']
    namespace: Literal[NAMESPACE]
    binary_sha256: Digest
    valid_after: UTCDateTime
    valid_before: UTCDateTime
    reviewers: Annotated[tuple[EnrolledReviewer,...],Field(min_length=1,max_length=32)]
    revoked_fingerprints: Annotated[tuple[str,...],Field(max_length=256)]

    @model_validator(mode='after')
    def exact_keys(self):
        if self.valid_before<=self.valid_after:raise ValueError('enrollment validity interval')
        if len({r.human_identity for r in self.reviewers})!=len(self.reviewers) or len({r.fingerprint for r in self.reviewers})!=len(self.reviewers):
            raise ValueError('duplicate reviewer identity/key')
        for reviewer in self.reviewers:
            blob=base64.b64decode(reviewer.public_key.split()[1],validate=True)
            expected=struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)
            if len(blob)!=51 or not blob.startswith(expected):raise ValueError('exact raw Ed25519 public key required')
            fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
            if fingerprint!=reviewer.fingerprint:raise ValueError('reviewer fingerprint mismatch')
        return self


class SSHHumanVerifier:
    """Read-only external enrollment. Agents must never provision this trust file."""
    def __init__(self,*,enrollment_path,expected_enrollment_sha256):
        self.enrollment_path=Path(enrollment_path)
        if type(expected_enrollment_sha256) is not str or len(expected_enrollment_sha256)!=64 or any(ch not in '0123456789abcdef' for ch in expected_enrollment_sha256):raise ValueError('pinned external enrollment SHA256 required')
        self.expected_enrollment_sha256=expected_enrollment_sha256

    def enrollment(self,now):
        data=protected_bytes(self.enrollment_path,65536)
        if hashlib.sha256(data).hexdigest()!=self.expected_enrollment_sha256:
            raise QualificationRejected('unverified_human_review','external enrollment changed or pin mismatched; refresh through administrator')
        try:value=HumanEnrollment.model_validate_json(canonical_json(decode_json(data,65536)))
        except ValueError as exc:raise QualificationRejected('unverified_human_review','invalid external enrollment') from exc
        if not value.valid_after<=now<=value.valid_before:raise QualificationRejected('unverified_human_review','external enrollment is outside its current validity interval')
        return value

    def verify(self,store,request_ref,attestation_ref,*,consumed_at=None):
        now=datetime.now(timezone.utc)
        request=read_local(store,request_ref,ReviewRequest,'m5-review-request')
        envelope=read_local(store,attestation_ref,DetachedAttestation,'m5-sshsig-attestation')
        raw=read_bytes(store,envelope.payload,65536,'m5-human-review-payload',True)
        try:payload=ReviewPayload.model_validate_json(canonical_json(decode_json(raw,65536)))
        except ValueError as exc:raise QualificationRejected('unverified_human_review','human payload schema/origin invalid') from exc
        if raw!=canonical_json(payload.model_dump(mode='json')):raise QualificationRejected('unverified_human_review','human payload must use the exact canonical signing bytes')
        if (payload.request,payload.task,payload.report,payload.policy,payload.challenge)!=(request_ref,request.task,request.report,request.policy,request.challenge):
            raise QualificationRejected('unverified_human_review','human attestation task/package/policy/challenge mismatch')
        when=now if consumed_at is None else consumed_at
        if not request.issued_at<=when<=request.expires_at or payload.decision!='approved':
            raise QualificationRejected('unverified_human_review','review decision or challenge validity does not approve this request')
        enrollment=self.enrollment(now)
        matches=[r for r in enrollment.reviewers if r.human_identity==payload.human_identity]
        if len(matches)!=1 or matches[0].fingerprint in enrollment.revoked_fingerprints:
            raise QualificationRejected('unverified_human_review','human signer absent or revoked in external enrollment')
        reviewer=matches[0]
        allowed=(reviewer.human_identity+' namespaces="'+NAMESPACE+'" '+reviewer.public_key+'\n').encode()
        signature=read_bytes(store,envelope.signature,16384,'m5-sshsig',True)
        result=verify_sshsig(raw,signature,allowed,reviewer.human_identity,NAMESPACE,binary_sha256=enrollment.binary_sha256)
        self.enrollment(datetime.now(timezone.utc))  # Refuse a changed trust snapshot during verification.
        result.update(human_origin_verified=True,enrollment_sha256=self.expected_enrollment_sha256,
            human_identity=payload.human_identity,fingerprint=reviewer.fingerprint,
            request=request_ref.model_dump(mode='json'),attestation=attestation_ref.model_dump(mode='json'))
        return payload,result
