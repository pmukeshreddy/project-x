# Environment qualification

`QualificationService.qualify(task_ref)` consumes an immutable BUILT TaskBundle,
actual GradingService/runtime and TaskBuilder. Its policy has one fixed seed and
a wall-time limit. The schedule is fixed:

1. Grade baseline once: its build executes, at least one mandatory behavioral
   case passes, compatibility checks pass, and at least one mandatory feature
   behavior fails. New feature API calls may be
   absent on baseline; compatibility cases use historically supported inputs.
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
negative tests. Incomplete execution remains fail-closed and is not redispatched.

Admission authenticates this task's selected report, current artifact hashes,
source projection, grade/reset evidence and exact schedule. It does not replay
construction or semantic repair history. The report remains controller-private.
`Factory.release(task_ref, accepted_report=report)` freezes the existing legal
BUILT → QUALIFIED → RELEASED task transitions without repackaging solver bytes.

Hidden comparisons, gold source, provider records and runtime receipts remain
outside the solver package. Runtime resource, network and process isolation are
unchanged. No real environment generation is part of the harness cleanup.
