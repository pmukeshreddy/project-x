"""Native signatures over NO_TASK_APPROVAL data and fail-closed enrollment checks."""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import uuid
import pytest
from feature_rl.qualification import QualificationRejected

_DIAGNOSTIC=None

def diagnostic_signature():
    global _DIAGNOSTIC
    if _DIAGNOSTIC is not None:return _DIAGNOSTIC
    root=Path(__file__).resolve().parents[1]/'.feature-rl/research/M5'/('native-tests-'+uuid.uuid4().hex)
    root.mkdir(parents=True,mode=0o700)
    env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC'}
    namespace='feature-rl-m5-diagnostic@example.invalid'
    payload=b'DIAGNOSTIC_ONLY actor=model decision=NO_TASK_APPROVAL\n'
    signatures=[];keys=[]
    for name in ('a','b'):
        key=root/('diagnostic-'+name)
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','M5 synthetic diagnostic only','-f',str(key)],env=env,check=True,timeout=5,capture_output=True)
        keys.append(Path(str(key)+'.pub').read_bytes())
        p=subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n',namespace],input=payload,env=env,check=True,timeout=5,capture_output=True)
        signatures.append(p.stdout)
    _DIAGNOSTIC=(payload,namespace,keys,signatures)
    return _DIAGNOSTIC


@pytest.mark.parametrize('defect',['none','replay','payload','key','principal','namespace','malformed'])
def test_native_signature_authenticates_only_exact_synthetic_bytes_and_allowed_key(defect):
    from feature_rl.qualification.attestation import verify_sshsig
    payload,namespace,keys,signatures=diagnostic_signature()
    principal='synthetic-diagnostic'
    allowed=principal.encode()+b' namespaces="'+namespace.encode()+b'" '+keys[0]
    sig=signatures[0]
    if defect=='payload':payload+=b'changed'
    if defect=='key':sig=signatures[1]
    if defect=='principal':principal='unallowed'
    if defect=='namespace':namespace='other@example.invalid'
    if defect=='malformed':sig=b'not a signature'
    if defect in {'none','replay'}:
        checked=verify_sshsig(payload,sig,allowed,principal,namespace)
        assert checked['exit_status']==0
        assert checked['payload_sha256']==hashlib.sha256(payload).hexdigest()
        assert checked['human_origin_verified'] is False
    else:
        with pytest.raises(QualificationRejected):verify_sshsig(payload,sig,allowed,principal,namespace)


def test_agent_owned_enrollment_cannot_turn_a_model_key_into_human_authority(tmp_path):
    from feature_rl.qualification.attestation import SSHHumanVerifier
    file=tmp_path/'enrollment.json';file.write_text('{}');file.chmod(0o444)
    verifier=SSHHumanVerifier(enrollment_path=file,expected_enrollment_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    with pytest.raises(QualificationRejected,match='externally administered'):
        verifier.enrollment(datetime.now(timezone.utc))


def test_native_verifier_never_runs_arbitrary_binary_path(tmp_path):
    from feature_rl.qualification.attestation import verify_sshsig
    fake=tmp_path/'fake-keygen';fake.write_text('#!/bin/sh\nexit 0\n');fake.chmod(0o755)
    with pytest.raises(QualificationRejected):verify_sshsig(b'diagnostic',b'bad',b'', 'x','diagnostic',binary=fake)


def test_human_payload_schema_rejects_model_actor_even_with_person_looking_identity():
    from feature_rl.qualification import ReviewPayload
    with pytest.raises(ValueError):ReviewPayload.model_validate_json('{"actor_type":"model","human_identity":"A Person","decision":"approved"}')
