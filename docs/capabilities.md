# Initial capability matrix (before source collection)

Scope is Python public CLI/API features; support must be demonstrated through faithful worker observations before task acceptance.

| Capability | Initial policy | Evidence required |
| --- | --- | --- |
| CLI arguments, stdout/stderr, exit code | Supported construction target | Real executable CLI with bounded external capture |
| JSON-compatible public API values/exceptions | Supported construction target | Adapter invokes actual API; controller compares ordinary data |
| Filesystem side effects | Conditional | Controller-owned independent inspection; declared allowed paths |
| Sequential local Redis state | Deferred until real lifecycle validation | Independent state observations, service/network/process isolation |
| Callback semantics and object identity | Unsupported until faithful adapter exists | Never silently serialized into an easier substitute |
| SaaS, performance-only, platform-specific behavior, wall-clock scheduling | Excluded initial track | Record typed unsupported disposition |
| Concurrency and cross-service transactions | Unsupported initial track | Separate contract/adapter/reset qualification required |

No capability in this matrix is a claim that an environment has executed. Rejections and exclusions remain in the construction funnel.
