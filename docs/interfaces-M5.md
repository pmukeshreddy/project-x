# Environment qualification

`QualificationService.qualify(task_ref)` consumes an immutable BUILT TaskBundle,
actual GradingService/runtime and TaskBuilder. Its policy has one fixed seed and
a wall-time limit. The schedule is fixed:

1. Grade baseline once: the old repository builds and executes, applicable
   pre-existing compatibility checks pass, and the intended feature is absent.
   New-feature cases may all fail, including missing APIs. They do not need to
   demonstrate a passing baseline case.
2. Grade the private historical gold projection once: every check passes.
3. Grade each plausible wrong implementation once: all are below full reward.
   A wrong implementation may violate several requirements. There is no target
   isolation, control diagnosis or extra seed schedule.
4. Save a source mutation, reset to the exact gold source, verify cleanup and the
   reset generation, then grade gold again with the same seed. Compare realized
   cases and raw behavioral observations, not merely the final reward.
5. Freeze a successful QualificationReport only when every gate passes.

Three wrong implementations are required (partial, happy path, hardcoded), plus
one regression implementation when compatibility obligations exist. Thus a
qualification uses six or seven grades. It stops at the first failed gate.
Unmeasured infrastructure failures and unclean execution never count as successful
negative tests.

Qualification calls the grader directly and freezes its result once. The private
QualificationReport retains the task, policy, gold projection, grade and reset
references. There are no qualification jobs, run bindings, accounting reconciliation
or secondary replay of the schedule, source projection, comparisons or costs.

Admission reads the report and immutable task hashes, checks successful disposition
and controller provenance, and preserves every frozen task field except state and
the qualification reference. `Factory.release(task_ref, accepted_report=report)`
saves BUILT → QUALIFIED → RELEASED manifests without repackaging solver bytes or
rerunning qualification. These private controller artifacts are authoritative.

Hidden comparisons, gold source, provider records and runtime receipts remain
outside the solver package. Runtime resource, network and process isolation are
unchanged. No real environment generation is part of the harness cleanup.
