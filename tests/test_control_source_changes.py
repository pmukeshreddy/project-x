"""File existence, permissions and source boundaries are controller facts."""
import pytest
from feature_rl.environments import SourceArchive, SourceFile, SandboxPolicy, SourceRejected
from feature_rl.verifiers.control_authoring import ControlProposal, TextReplacement


def proposal(path, before, after):
    from feature_rl.verifiers.control_authoring import SourceChange
    return ControlProposal(files=(SourceChange(path=path, replacements=(TextReplacement(before=before, after=after),)),), deletions=(), rationale='Plausible incomplete implementation')


def test_existing_file_is_edited_and_mode_is_preserved():
    baseline = SourceArchive({'src/tool.py': SourceFile(b'value = 1\n# unseen\n', True)})
    delta = SourceArchive.read(proposal('src/tool.py', 'value = 1', 'value = 2').delta(baseline), SandboxPolicy(platform='linux/arm64'))
    assert delta.files['src/tool.py'] == SourceFile(b'value = 2\n# unseen\n', True)


def test_missing_file_is_created_without_model_operation_choice():
    delta = SourceArchive.read(proposal('src/new.py', '', 'value = 2\n').delta(SourceArchive({})), SandboxPolicy(platform='linux/arm64'))
    assert delta.files['src/new.py'] == SourceFile(b'value = 2\n', False)


@pytest.mark.parametrize('path,before', [('src/a.py', ''), ('src/missing.py', 'value'), ('../escape', '')])
def test_controller_rejects_invalid_path_or_anchor(path, before):
    baseline = SourceArchive({'src/a.py': SourceFile(b'value = 1\n', False)})
    with pytest.raises((ValueError, SourceRejected)): proposal(path, before, 'wrong').delta(baseline)
