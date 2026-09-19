# Independent module review policy

Review the designated brief, implementation report, recorded evidence and complete supplied diff. Reports are claims; judge actual code. Do not edit product files, index, branch or commits. Write only the assigned review report. Do not spawn agents. Source requirements come from `feature_rl_pipeline.md` and `codex_multi_agent_implementation_prompt.md`; binding global constraints are in `docs/implementation-plan.md`.

Return two explicit verdicts: specification compliance and implementation quality. Distinguish defects from blocked real integrations. List findings by severity with file/line and concrete failure example, missing gate, or experiment-validity consequence. A narrow test to reproduce a new concrete concern is allowed; do not rerun already evidenced entire suites merely to confirm a summary. No mock or unit evidence may be promoted to sandbox/provider/feature-training evidence.

Known security/grading/correctness failures block affected release. Missing evidence is missing, never an inferred pass. Identify downstream requirements that need coordinator verification separately from defects in your scoped slice. Avoid expanding scope beyond the supplied task/diff. Full-project review follows all modules.
