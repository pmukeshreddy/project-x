# Coordinator source notes

These are discovery observations, not accepted task evidence. M1 must archive source responses and reconstruct Git ancestry through its implementation.

- First source: https://github.com/pallets/click/pull/3228, linked request https://github.com/pallets/click/issues/3107. Live PR page inspected 2026-09-19. It reports integration commit prefix `831c8f0` on 2026-04-29, with two PR commits. Full ancestry and commit IDs remain M1's gate.
- Current edited request text cannot prove preimplementation wording. Distinguish archived retrieval time from claimed historical creation time and record edit-history availability.
- PR includes command suggestions and companion option formatting changes. Contract must disclose any selected public exception/normalization obligations and avoid requiring unrelated changes implicitly.
- Docker boundary guidance: https://docs.docker.com/engine/security/. Pin image digests and prove namespaces, no networking except declared services, dropped capabilities, nonroot worker, resource limits and no controller sockets/mounts.
- Harbor verifier documentation: https://docs.harborframework.com/core-concepts/tasks/separate-verifier; network policy https://docs.harborframework.com/core-concepts/tasks/network-policies. Separate verifier alone is insufficient if candidate code executes inside it. M7 must inspect pinned automatic artifact transfers and real sandbox enforcement.

No repository code, dependencies, tests, feature probes or model updates have been run by these observations.
