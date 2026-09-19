"""Bounded inert H projection diagnostics, not feature qualification."""
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import SandboxPolicy, SourceFile
from feature_rl.submission import SubmissionService
from m4_fixtures import replace_artifact
from m5_fixtures import task_fixture

@pytest.fixture
def store(tmp_path):return ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)


def test_reference_projection_retains_exact_h_and_accounts_for_excluded_paths(store):
    from feature_rl.qualification import derive_reference
    task_ref=task_fixture(store);task=store.get_artifact(task_ref)
    projection=derive_reference(store,task_ref,SandboxPolicy())
    assert projection.task==task_ref and projection.reference==task.reference_solution
    assert projection.baseline==task.baseline and projection.source_pair==task.source_pair
    assert [(p.path,p.action) for p in projection.paths]==[
        ('docs/use.rst','excluded_documentation'),('src/click/__init__.py','included_implementation'),
        ('tests/test_example.py','excluded_tests')]
    service=SubmissionService(store=store,policy=SandboxPolicy())
    rebuilt=service.resolve(projection.submission,task.baseline,store.get_artifact(task.contract).allowed_changes)
    assert rebuilt.files['src/click/__init__.py'].data==b'# synthetic reference\n'
    assert rebuilt.files['docs/use.rst'].data==b'synthetic old docs\n'
    assert service.source(projection.projected_source).files==rebuilt.files
    assert store.get_artifact(task_ref).reference_solution==task.reference_solution


@pytest.mark.parametrize('category',['mixed','unrelated','dependency_build'])
def test_reference_projection_never_drops_unsupported_changed_paths(store,category):
    from feature_rl.qualification import derive_reference, QualificationRejected
    record=c.ChangedFile(path='src/click/__init__.py',category=category,rationale='Synthetic unsupported control')
    task=task_fixture(store,changes={'src/click/__init__.py':SourceFile(b'# changed\n',False)},classifications=(record,))
    with pytest.raises(QualificationRejected,match='unsupported_semantics'):derive_reference(store,task,SandboxPolicy())


@pytest.mark.parametrize('defect',['omitted','duplicate','extra','disguised_implementation','wrong_h','out_of_policy','executable'])
def test_reference_projection_rejects_mismatched_classification_or_submission_policy(store,defect):
    from feature_rl.qualification import derive_reference, QualificationRejected
    changes={'src/click/__init__.py':SourceFile(b'# changed\n',defect=='executable')}
    task_ref=task_fixture(store,changes=changes);task=store.get_artifact(task_ref);pair=store.get_artifact(task.source_pair)
    files=list(pair.changed_files)
    if defect=='omitted':files=[c.ChangedFile(path='docs/use.rst',category='documentation',rationale='Incorrect fixture')]
    if defect=='duplicate':files=files*2
    if defect=='extra':files.append(c.ChangedFile(path='missing.py',category='implementation',rationale='Incorrect fixture'))
    if defect=='disguised_implementation':files=[c.ChangedFile(path=files[0].path,category='documentation',rationale='Incorrect fixture')]
    if defect in {'omitted','duplicate','extra','disguised_implementation'}:
        pref=replace_artifact(store,task.source_pair,changed_files=[x.model_dump(mode='json') for x in files])
        task_ref=replace_artifact(store,task_ref,source_pair=pref)
    if defect=='wrong_h':task_ref=replace_artifact(store,task_ref,reference_solution=task.baseline.model_copy(update={'visibility':c.Visibility.PRIVATE}))
    if defect=='out_of_policy':
        contract=store.get_artifact(task.contract)
        rules=contract.allowed_changes.model_dump(mode='json');rules['forbidden_paths']=['src/click']
        task_ref=replace_artifact(store,task_ref,contract=replace_artifact(store,task.contract,allowed_changes=rules))
    with pytest.raises(QualificationRejected):derive_reference(store,task_ref,SandboxPolicy())


def test_reference_projection_handles_source_deletion_with_evidence(store):
    from feature_rl.qualification import derive_reference
    task=task_fixture(store,changes={'src/click/__init__.py':None})
    projection=derive_reference(store,task,SandboxPolicy())
    included=next(p for p in projection.paths if p.action=='included_implementation')
    assert included.before_sha256 is not None and included.after_sha256 is None
    assert 'src/click/__init__.py' not in SubmissionService(store=store,policy=SandboxPolicy()).source(projection.projected_source).files
