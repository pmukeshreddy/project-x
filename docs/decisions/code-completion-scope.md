# Current implementation scope

The user's latest instruction on 2026-09-19 is authoritative over the earlier experimental stopping rule:

> Current scope is complete code implementation. GPU training and experimental results are deferred. Finish all modules and their integration, run available non-GPU checks, and provide reproducible training/evaluation commands. Mark GPU execution as unverified; do not treat it as a blocker to completing the code or substitute fake results.

Complete and independently review M0–M8 product code, their actual interfaces, the CLI, non-GPU acceptance checks, and reproducible setup/training/evaluation commands. An unavailable GPU, qualified experimental dataset, or human task approval does not prevent finishing independent code. Those prerequisites remain enforced at the execution boundary. Never manufacture a released task, approval, policy trajectory, checkpoint, metric, or successful framework execution to make a check pass.

Maintain separate code and execution status. A module can be implemented, reviewed and integrated while its CUDA execution or real-data experiment is explicitly unverified. Preserve the specification's construction, qualification, admission, training-signal, optimizer/reload and study-validity gates in runnable code; do not remove or bypass them. Commands must invoke implemented operations with explicit configurations and fail with a precise prerequisite error when required artifacts or hardware are unavailable.

Continue local real-runtime and provider checks that are necessary for code verification, within declared bounds. Reuse existing verification evidence and do not repeat consumed native diagnostics or historical suites without a changed risk. Model-based first-feature construction may be attempted with a separately declared bounded profile; a recorded capacity/semantic failure must not stall the rest of the implementation or be replaced with a handwritten production answer. Unit fixtures and fault injection remain clearly identified as diagnostics.

The earlier approximately twenty-task pilot, human acceptance, genuine released-feature episode, training-signal study, CUDA optimizer/reload run, and held-out A/B/C/D comparison are execution milestones, not prerequisites for this code-completion deliverable. Their implementations, validation, configurations, and reproducible commands are still required. Final reporting distinguishes implemented/reviewed code, actually executed checks, and unverified experiments.

Ownership, dependency order and independent review remain unchanged. The same owner fixes its module. Root coordinates interfaces/integration and records evidence; it does not silently take over module product code.
