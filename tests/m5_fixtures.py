"""Synthetic M5 mechanism fixtures; no historical source or human approval."""
from datetime import datetime, timezone
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import SourceArchive, SourceFile
from feature_rl.history.classification import classify_changed_files
from m4_fixtures import diagnostic, replace_artifact


def task_fixture(store, *, changes=None, classifications=None):
    files={'src/click/__init__.py':SourceFile(b'# synthetic baseline\n',False),
           'docs/use.rst':SourceFile(b'synthetic old docs\n',False),
           'tests/test_example.py':SourceFile(b'# synthetic old test\n',False)}
    newer=dict(files)
    newer.update(changes if changes is not None else {
        'src/click/__init__.py':SourceFile(b'# synthetic reference\n',False),
        'docs/use.rst':SourceFile(b'synthetic new docs\n',False),
        'tests/test_example.py':SourceFile(b'# synthetic new test\n',False)})
    newer={name:entry for name,entry in newer.items() if entry is not None}
    baseline=store.put_bytes(SourceArchive(files).to_tar(),'source-archive',c.Visibility.AUTHORING)
    reference=store.put_bytes(SourceArchive(newer).to_tar(),'source-archive',c.Visibility.PRIVATE)
    task_ref=diagnostic(store,baseline=baseline)
    task=store.get_artifact(task_ref);now=datetime(2026,9,19,tzinfo=timezone.utc)
    proof=task.provenance.evidence
    relationship=c.CommitRelationship(integration='squash',target_before='1'*40,integrated_after='2'*40,
        implementation_commits=('2'*40,),parents=('1'*40,),evidence=proof)
    candidate=c.CandidateRecord(kind='CandidateRecord',schema_version=2,visibility=c.Visibility.PRIVATE,
        provenance=task.provenance,costs=task.costs,provenance_label='reconstructed_specification',
        repository_url='https://example.invalid/synthetic',repository_family=task.repository_family,
        request_lineage=task.request_lineage,partition=task.partition,
        sources=(c.SourceSnapshot(url='https://example.invalid/synthetic',content=task.solver_view.instruction,
            retrieved_at=now,published_at=None,edited_at=None,edit_history='unavailable',media_type='text/plain',redirect_chain=None),),
        license=c.LicenseRecord(spdx_id=None,license_text=task.solver_view.instruction,status='verified',evidence=proof),
        commits=relationship,screening=c.ScreeningDecision(disposition=c.Disposition.SUCCESS,reason='Synthetic fixture only',evidence=proof))
    cref=store.put_artifact(candidate)
    changed=tuple(sorted(name for name in set(files)|set(newer) if files.get(name)!=newer.get(name)))
    categories=classify_changed_files(changed).changed_files if classifications is None else classifications
    pair=c.SourcePair(kind='SourcePair',schema_version=2,visibility=c.Visibility.PRIVATE,provenance=task.provenance,
        costs=task.costs,provenance_label='reconstructed_specification',candidate=cref,baseline_commit='1'*40,
        reference_commit='2'*40,baseline=baseline,reference=reference,relationship=relationship,
        changed_files=categories,admissible_cutoff=now,verification=proof)
    pair_ref=store.put_artifact(pair)
    verifier=replace_artifact(store,task.private_oracle,source_pair=pair_ref)
    return replace_artifact(store,task_ref,source_pair=pair_ref,reference_solution=reference,private_oracle=verifier)
