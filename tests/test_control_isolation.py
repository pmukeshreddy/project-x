"""Isolation planning cannot omit obligations or replace execution evidence."""
import pytest
from feature_rl.verifiers.control_isolation import IsolationAssessment, require_isolated_execution
from test_qualification_controls import checked, receipt


def assessment(**changes):
    values = dict(disposition='inseparable', targets=('compat',), preserved=('feature',),
        coupled=('feature',), explanation='The feature requires the preserved result.', mutation='')
    values.update(changes)
    return IsolationAssessment(**values)


@pytest.mark.parametrize('changes', [
    {'targets': ('feature',)}, {'preserved': ()}, {'preserved': ('feature', 'feature')},
    {'coupled': ('compat',)}, {'coupled': ()}, {'mutation': 'Return a guessed constant'},
    {'disposition': 'separable', 'coupled': (), 'mutation': ''},
])
def test_isolation_assessment_rejects_changed_targets_omissions_and_fake_mutations(changes):
    with pytest.raises(ValueError):
        assessment(**changes).validate_obligations(checked().contract, ('compat',))


def test_inseparable_target_is_explicit_without_producing_a_control():
    value = assessment().validate_obligations(checked().contract, ('compat',))
    assert value.disposition == 'inseparable'
    assert not value.mutation


@pytest.mark.parametrize('observed', [
    receipt(failed=('feature', 'compat')), receipt(failed=('compat',)),
    receipt(status='candidate_failure'), receipt(reward=1),
])
def test_authoring_execution_rejects_collateral_failure_crashes_and_false_passes(observed):
    with pytest.raises(ValueError):
        require_isolated_execution(None, checked(), observed, ('feature',))


def test_authoring_execution_uses_the_exact_semantic_gate():
    assert require_isolated_execution(None, checked(), receipt(failed=('feature',)), ('feature',)).passed


def test_frozen_isolation_task_cannot_silently_skip_execution(tmp_path):
    from feature_rl import contracts as c
    from feature_rl.requirements import GenerationCandidate
    from test_control_authoring import control_fixture
    store, inputs, sources, _, _, request, runner, service = control_fixture(tmp_path)
    task = c.ArtifactRef(sha256='f'*64, kind='TaskBundle', schema_version=1,
        visibility=c.Visibility.PRIVATE, encoding='json')
    inputs = inputs.model_copy(update={'isolation_task': task, 'isolation_seeds': (11,23,47)})
    with pytest.raises(ValueError, match='actual control isolation executor'):
        service.generate((GenerationCandidate(request=request),), inputs, sources)
    assert not runner.calls
