# M6 builder round 1 correction

The single P2 in `docs/reviews/M6-factory-build-round1.md` is corrected. Assembly
checks the serialized solver inventory against the existing 1 MiB consumer cap
before publishing that inventory or freezing a successful result. Generated
instruction/runtime documents and the normalized workspace also use their
corresponding existing reader limits before publication. Limits were not widened;
recovery still reuses exact frozen bytes.

The 1,902-file long-path regression first reproduced `BuildRecoveryRequired` after
the over-limit inventory had frozen. After the correction it returns a typed
`unsupported_semantics` failed construction with attributable measured construction
wall time and explicit unknown storage cost. No successful frozen receipt or
inventory is published. Registry accounting, completed-result replay and recovery
retain the identical failed result and do not append events. Earlier published
public component CAS objects can remain unselected; no BUILT root references them.

Focused verification: **19 factory tests passed**, pytest **8.10 seconds**, exit 0.
The [receipt](../evidence/M6/factory-build-fix1/receipt.json) binds the actual source
hashes before/after, observed Git revision, command and timestamps; the
[output](../evidence/M6/factory-build-fix1/focused.txt) preserves the result. The new
test is the review's inert synthetic archive case with diagnostic wheel substitutions.
No Docker, model, historical source execution or broad suite ran for this fix.

The prior 54-test evidence remains unchanged and covers the original builder and
registry slice. The separately authorized real Docker fixture integration is not
claimed by this correction.
