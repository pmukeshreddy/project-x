"""Freeze actual Factory authoring/repair selection for subsequent construction."""
from feature_rl import contracts as c
from feature_rl.qualification import RepairAttempt,RepairHistory
from .authoring_models import AuthoringFrontier
from .authoring import jobs,read_authoring_receipt
from .construction import put,references,incomplete_history
from .packaging import read_record,typed,document


def selected_history(factory,request):
    """Complete only the explicitly frozen controller scope; imports stay incomplete."""
    fronts=[ref for ref in factory.registry.trace(request.candidate).artifacts
        if ref.kind=='m6-authoring-frontier'
        and read_record(factory.store,ref,AuthoringFrontier,'m6-authoring-frontier').candidate==request.candidate]
    if not fronts:return None
    if len(fronts)!=1:raise ValueError('ambiguous authoring frontier before construction')
    front_ref=fronts[0];front=read_record(factory.store,front_ref,AuthoringFrontier,'m6-authoring-frontier')
    if (front.source!=request.source or front.source_pair!=request.inputs.source_pair
            or front.environment!=request.inputs.environment):
        raise ValueError('construction changed its frozen authoring frontier')
    selected=jobs(factory,request.candidate)
    if any(job.state!='completed' or job.result is None for job,_ in selected):
        raise ValueError('authoring attempt unresolved; reconcile before freezing construction history')
    from .repair_accounting import classifications
    transport_proofs=classifications(factory,selected)
    order={};after=0
    while True:
        batch=factory.registry.events(after=after,limit=1000)
        if not batch:break
        for event in batch:
            if event.action=='claim':order[event.data['claim']['job_id']]=event.sequence
        after=batch[-1].sequence
    selected.sort(key=lambda entry:order[entry[0].job_id])
    base_ref=incomplete_history(factory,request)
    base=read_record(factory.store,base_ref,RepairHistory,'m5-repair-history')
    attempts=list(base.attempts);last={item.stage:item.after for item in attempts}
    journal_refs=[*base.journal_refs,front_ref,front.batch,*transport_proofs.values()]
    from .authoring import historical_settings
    journal_refs.extend(ref for job,_ in selected
        if (ref:=historical_settings(factory,job).semantic_repair_authorization) is not None)
    initial=list(base.initial_evidence);terminals={};complete=bool(selected) and front.scope=='factory-controlled-after-source-disposition'
    for job,author in selected:
        receipt_ref=job.result.artifacts[-1];receipt=read_authoring_receipt(factory.store,receipt_ref)
        if (author.frontier!=front_ref or receipt.claim.job_id!=job.job_id or receipt.request!=job.spec.inputs[1]
                or job.result.artifacts!=(*receipt.outputs,receipt_ref) or receipt.disposition!=job.result.disposition
                or not any(attempt.claim==receipt.claim and attempt.state=='completed' for attempt in factory.registry.attempts(job.job_id))):
            raise ValueError('repair history lacks the exact selected authoring attempt/result')
        complete=complete and author.origin=='factory_dispatch'
        journal_refs.extend((job.spec.inputs[1],receipt_ref,*receipt.journal_refs))
        if author.repair:
            before=last.get(author.stage)
            if before is None:
                before=put(factory,{'version':'m6-stage-before-v1','frontier':document(front_ref),
                    'stage':author.stage,'previous_role':document(author.previous)},'m6-stage-before',
                    dependencies=(front_ref,author.previous))
            attempts.append(RepairAttempt(stage=author.stage,before=before,after=receipt_ref,
                diagnosis=author.call.generation.diagnosis,change=author.call.generation.changed_input,
                evidence=job.result.evidence,costs=receipt.costs))
            last[author.stage]=receipt_ref
        else:initial.extend(job.result.evidence)
        terminals[author.lane]=(job,author,receipt)
    # Every selected task artifact and control must come from the terminal
    # authenticated producer lane. Externally supplied artifacts stay incomplete.
    outputs={ref for _,_,receipt in terminals.values() if receipt.disposition==c.Disposition.SUCCESS for ref in receipt.outputs}
    if request.inputs.verifier not in outputs:
        from .checker import selected_assembly
        assembly=selected_assembly(factory,request.candidate,request.inputs.verifier,outputs)
        if assembly is not None:
            outputs.add(request.inputs.verifier)
            journal_refs.append(assembly)
    complete=complete and {request.inputs.contract,request.inputs.scenario_plan,request.inputs.verifier}<=outputs
    verifier=typed(factory.store,request.inputs.verifier,c.VerifierBundle)
    controls=[]
    from feature_rl.verifiers.control_authoring import ControlRecord
    for ref in outputs:
        if ref.kind=='m4-control-record':controls.append(read_record(factory.store,ref,ControlRecord,ref.kind).control)
    complete=complete and all(control in controls for control in verifier.controls)
    history=RepairHistory(candidate=request.candidate,complete=complete,initial_evidence=tuple(initial),
        attempts=tuple(attempts),journal_refs=tuple(dict.fromkeys(journal_refs)))
    return put(factory,history,'m5-repair-history',dependencies=references(document(history)))
