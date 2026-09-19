# Dependency-ordered independent implementation slices

The earlier coordinator rule of one product writer globally was a conservative scheduling choice. The user's current deliverable is complete code. Independent components can now proceed concurrently when their actual dependencies are already reviewed and their writable paths, test state and resources do not overlap. This does not authorize dependent implementers against hypothetical APIs or transfer module ownership.

At most two product owners work concurrently, leaving root and one review slot within the four-agent limit. Each owner writes only its explicit module/slice files, commits only owned paths, and runs focused checks in separate temporary/private state. No concurrent shared dependency/configuration changes. Native authoring and heavy runtime/CPU diagnostics are scheduled separately. Root runs the integrated suite after reviewed checkpoints when product files are stable.

The first such pair is M2 authoring/scenarios and M6's standalone registry/event/job/dependency core. The registry consumes existing M0 references, artifacts, costs and operation records; it does not yet import or invent M2 authoring, M4 grading, M5 admission or M7/M8 service APIs. Its owner later implements factory orchestration and CLI against those actual reviewed interfaces. No placeholder construct/qualify/run/train/evaluate methods are permitted in the registry slice.

Every independently reviewed slice receives an honest partial-module status, complete working code for its scoped behavior, meaningful checks and evidence. The same owner handles the rest of its module and integration fixes. The final integrated review still covers all modules and boundaries.
