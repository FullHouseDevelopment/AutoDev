# C# migration review contract

Every behavioral PR targeting `migration/csharp-host` should be reviewable as one canonical capability replay rather than as a batch translation of Python files.

Use the canonical migration ledger in `docs/csharp-migration-ledger.md` to choose the slice and dependency order.

## Required PR metadata

Each behavioral migration PR should include these sections:

```text
Migration slice
---------------
Mxx — <canonical capability>

Historical anchor
-----------------
#<issue> / PR #<pr>

Canonicalized by
----------------
#<later issue>, #<later issue>, ...

Python reference
----------------
<current Python modules/tests that define observable behavior>

C# implementation
-----------------
<new C# projects/files/tests>

Compatibility surface
---------------------
<CLI/state/filesystem/Git/GitHub/provider/etc. observations compared>

Intentional differences
-----------------------
None

Evidence
--------
<differential fixtures/tests and any non-differential characterization evidence>
```

`Intentional differences` must say `None` unless the PR deliberately changes a supported contract. A deliberate behavior change belongs in a separately reviewed product issue rather than being hidden inside a migration.

## Differential evidence rules

The Python implementation on the migration base is the executable behavioral reference until the production cutover.

Normalize only noise that the PR explicitly declares non-contractual. The M00 harness supports line-ending normalization and replacement of the scenario workspace root; later slices should add normalizers only when a concrete compatibility fixture proves they are needed.

Prefer observations at externally meaningful boundaries:

- exit code and contractual stdout/stderr;
- durable JSON and selected filesystem mutations;
- Git commands and resulting refs/commits;
- GitHub/provider request envelopes through mocks or fakes;
- selected workflow stage and terminal state.

Do not assert equivalence by comparing implementation structure, class names, call counts that are not contractual, or incidental temporary files.

## Migration-only scaffold

M00 intentionally exposes only the candidate command `migration-probe`. It is not a supported AutoDev product command and must not be wired into installers or the public `autodev` launcher. Its purpose is to prove that the compatibility harness can launch and observe the C# candidate process before any real behavior is claimed as migrated.
