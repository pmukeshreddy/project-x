# Project dependencies over the pinned SkyRL environment

Read alongside the M7 compatibility audit and worker-endpoint decision. These are installation requirements for code reproducibility; CUDA compatibility remains unverified.

The retained SkyRL `skyrl-v0.3.0` lock pins Pydantic 2.13.4/core 2.46.4, while this project's reviewed configuration requires Pydantic 2.13.5/core 2.46.5. Harbor's declared requirement is `pydantic>=2.11.7`, and inspected SkyRL metadata does not directly constrain Pydantic. The upstream annotated-types 0.7.0, typing-extensions 4.15.0 and typing-inspection 0.4.2 satisfy the newer project's other direct Pydantic requirements. This source inspection does not prove compatibility of every installed transitive package.

Use a deliberate two-package project overlay after installing the exact upstream lock with the FSDP and Harbor extras. M0 owns the hash-pinned overlay, and the separately built project wheel must have its own recorded hash. The reviewed project Pydantic pin does not change. Preserve upstream Torch 2.11/cu128, vLLM 0.23/cu129, custom router and patched-wheel sources; ordinary PyPI resolution can choose an incompatible CUDA variant.

The current project lock already identifies:

- `pydantic-2.13.5-py3-none-any.whl`, 472589 bytes, SHA256 `346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73`.
- `pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`, 2066284 bytes, SHA256 `0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2`.

The installation sequence must limit the overlay to approved hashed wheels with `--no-deps --require-hashes --no-index --find-links`, then verify installed metadata dependencies and compare the package inventory against the frozen upstream plus explicit overlay. Do not claim the environment still matches the unmodified upstream lock. Launch through its interpreter or `uv run --offline --no-sync`; ordinary subsequent sync can restore the older pair, and `--isolated` would create a different environment. M7 confirmed these flag semantics using local uv0.11.8; pinned-image uv0.9.4 execution remains unverified and must be handled explicitly by setup verification.

CPU tensor/optimizer diagnostics use a separate environment on this Mac. Never install Torch or alter dependency detection in the already measured MLX authoring environment. A CPU diagnostic environment proves only the exercised mathematics/serialization/checkpoint behavior, not CUDA/FSDP/vLLM compatibility or feature-learning results. M0 is preparing the exact compatible local wheel closure before any installation is authorized by its next implementation brief.
