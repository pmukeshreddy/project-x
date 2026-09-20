"""Explicit supervised imports. Source stays inert; targets use the runner protocol."""
from dataclasses import dataclass
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.agents import AgentRunner
from feature_rl.agents.protocol import parse_action, execution_request
from feature_rl.environments import SourceArchive
from .data import TrainingDataGate, tokenize_supervision
from .torch_backend import CausalTurn

# A deterministic command target, executed only if a student later emits it in M3.
# No synthetic observation or successful execution receipt is appended to SFT data.
_APPLY = '''import json,pathlib,sys
x=json.load(sys.stdin);root=pathlib.Path('/workspace/source')
def path(name):
 p=(root/name).resolve()
 if not p.is_relative_to(root) or p==root: raise ValueError('outside source')
 return p
for name in x['delete']: path(name).unlink()
for name,content in x['write'].items():
 p=path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding='utf-8');p.chmod(0o644)
'''


def render_source_action(baseline: SourceArchive, source: SourceArchive, rules) -> str:
    """One bounded source-edit command; rejects solutions outside the harness capacity."""
    from feature_rl.submission.source import change_path, validate_rules
    rules=validate_rules(rules)
    source.validate_changes(baseline,rules.source_roots,rules.forbidden_paths)
    deletes=sorted(baseline.files.keys()-source.files.keys())
    writes={}
    for name in sorted(source.files):
        entry=source.files[name]
        if baseline.files.get(name)==entry: continue
        change_path(name,rules)
        if entry.executable: raise ValueError('Executable supervised source unsupported')
        writes[name]=entry.data.decode('utf-8',errors='strict')
    for name in deletes: change_path(name,rules)
    if not writes and not deletes: return '{"action":"submit"}'
    target=canonical_json({'action':'command','argv':['python','-I','-c',_APPLY],
        'stdin':canonical_json({'write':writes,'delete':deletes}).decode(),'timeout_seconds':30.}).decode()
    execution_request(parse_action(target),30.)  # Exact production schema and byte bounds.
    return target


@dataclass(frozen=True)
class SupervisedExample:
    task: c.ArtifactRef
    submission: c.ArtifactRef
    grade: c.ArtifactRef
    turns: tuple[CausalTurn,...]
    evidence: tuple[c.ArtifactRef,...]


class DemonstrationImporter:
    def __init__(self,runner: AgentRunner):
        if type(runner) is not AgentRunner: raise TypeError('Actual AgentRunner required')
        self.runner=runner
        self.gate=TrainingDataGate(store=runner.store,admit=runner.lifecycle.resolve_released,
            grader_revision=runner.grader.revision,runner=runner)

    def source(self,*,task,submission,grade,policy) -> SupervisedExample:
        """Import an explicitly supplied M4-verified source solution for the SFT arm."""
        runner=self.runner;admitted=self.gate.admit_task(task)
        runner.registry.assert_usable(task);runner.registry.assert_usable(grade)
        receipt=self.gate._grade(grade,task=task,submission=submission,reward=1)
        if receipt.source is None: raise ValueError('Verified supervised source missing')
        contract=runner.store.get_artifact(admitted.contract)
        source=runner.grader.submissions.resolve(submission,admitted.baseline,contract.allowed_changes)
        graded=runner.grader.submissions.source(receipt.source)
        if source!=graded: raise ValueError('Supervised source differs from exact graded submission')
        baseline=runner.grader.submissions.source(admitted.baseline)
        target=render_source_action(baseline,source,contract.allowed_changes)
        runner.backend.verify_policy(policy)
        messages=[{'role':'system','content':runner._bytes(policy.system_prompt,262144).decode()}]+runner._messages(admitted)
        context,ids=runner.backend.render(messages)
        tokenizer=getattr(runner.backend,'tokenizer',None)
        if tokenizer is None: raise TypeError('Trusted native deterministic tokenizer required for source SFT')
        encode=lambda text:tuple(tokenizer.encode(text,add_special_tokens=False))
        if encode(context)!=ids: raise ValueError('Source SFT context differs from runner rendering')
        turn=tokenize_supervision(context,target,encode_context=encode,encode_target=encode,
            max_seq_len=runner.backend.max_seq_len)
        if any(t>=runner.backend.vocab_size or t<0 for t in turn.context+turn.targets):
            raise ValueError('Supervised tokens outside declared vocabulary')
        return SupervisedExample(task,submission,grade,(turn,),(task,submission,grade,receipt.source,policy.system_prompt))

    def trajectory(self,ref,*,policy) -> SupervisedExample:
        """Retain actual successful runner contexts/targets, strip behavior probabilities."""
        runner=self.runner;runner.registry.assert_usable(ref)
        record=runner.store.get_artifact(ref)
        if not isinstance(record,c.RolloutRecord): raise ValueError('RolloutRecord demonstration required')
        self.gate.admit_task(record.task)
        outcome=runner.validate_record(record)
        receipt=self.gate._episode_grade(record,outcome.case_seed)
        if record.reward!=1 or not record.training_eligible or receipt is None:
            raise ValueError('Measured successful exact-token demonstration required')
        if (record.policy.identity.tokenizer_digest!=policy.identity.tokenizer_digest
            or record.policy.harness_version!=policy.harness_version
            or record.policy.system_prompt!=policy.system_prompt):
            raise ValueError('Demonstration tokenizer/harness/system prompt differs')
        runner.backend.verify_policy(policy)
        turns=[]
        from .core import validate_trace
        for step in record.steps:
            trace=step.token_trace
            validate_trace(trace,context=trace.context_token_ids,policy_version=record.policy.policy_version,
                vocab_size=runner.backend.vocab_size,max_seq_len=runner.backend.max_seq_len)
            turns.append(CausalTurn(trace.context_token_ids,trace.sampled_token_ids,trace.assistant_loss_mask))
        grade=next(x for e in record.grading_evidence for x in e.artifacts if x.kind=='m4-grade-receipt')
        return SupervisedExample(record.task,record.submission,grade,tuple(turns),(ref,grade))
