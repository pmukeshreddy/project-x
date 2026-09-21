"""Control failure classification on typed retained comparison receipts."""
from types import SimpleNamespace

import pytest

from feature_rl.qualification.controls import diagnose_control
from test_qualification_determinism import fixture, observed_receipt


@pytest.mark.parametrize('expected', ['false_acceptance', 'oracle_disagreement', 'false_rejection'])
@pytest.mark.parametrize('verified_origin', [False, True])
def test_observed_failure_keeps_exact_code_only_with_verified_control_origin(tmp_path, expected, verified_origin):
    q, task = fixture(tmp_path)
    checked, receipt = observed_receipt(q, task,
        output=b'ok value' if expected=='false_acceptance' else b'missing',
        exit_code=3 if expected=='oracle_disagreement' else 0)
    positive = expected=='false_rejection'
    control = SimpleNamespace(control_id='observed', category='alternative_positive' if positive else 'adversarial',
        requirement_ids=() if positive else ('echo',))
    binding = receipt.manifest
    origins = {'observed': (None if positive else 'observation_spoofing', (binding,), positive)} if verified_origin else {}
    diagnosis, outcome = diagnose_control(q, checked, control, receipt, binding, origins)
    assert not outcome.passed
    assert outcome.code == (expected if verified_origin else 'provisional')
    assert diagnosis.validity == 'unresolved'
    assert diagnosis.evidence[0].artifacts[0] == binding


def test_unverified_alternative_independence_stays_provisional(tmp_path):
    q, task = fixture(tmp_path)
    checked, receipt = observed_receipt(q, task, output=b'missing')
    control = SimpleNamespace(control_id='alternative', category='alternative_positive', requirement_ids=())
    _, outcome = diagnose_control(q, checked, control, receipt, receipt.manifest,
        {'alternative': (None, (receipt.manifest,), False)})
    assert outcome.code == 'provisional'
