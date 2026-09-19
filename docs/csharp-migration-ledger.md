# Canonical C# migration ledger

Branch: `migration/csharp-host`  
Base at creation: `develop@abca0424ec0bf5c1324fff4a604fefada61f2f20`  
Target runtime: .NET 10 / C#  
Migration goal: replace AutoDev's production Python host while preserving current observable behavior and Linux/Windows support.

## Migration rule

This is a **canonical behavioral replay**, not a file-by-file translation and not a literal replay of obsolete intermediate designs.

Historical issues/PRs define review-sized capability slices. Later fixes, redesigns, and hardening issues are folded into the earliest still-meaningful capability they amend. The C# implementation must reproduce the **current canonical behavior** of that lineage, not intentionally recreate superseded bugs or architectures.

A migration PR should answer one review question:

> Does this C# slice reproduce the current behavior of this capability lineage?

Each behavioral migration PR should record:

- historical anchor issue/PR;
- later issues that amend or supersede it;
- Python reference modules/tests;
- C# implementation modules/tests;
- observable compatibility surface;
- intentional differences, ideally none;
- differential/characterization evidence.

## Branch model

- `develop` remains the normal Python product line while the migration is in progress.
- `migration/csharp-host` is the long-lived integration branch for the C# host.
- Individual migration PRs branch from and target `migration/csharp-host`.
- Do not merge `develop` into the migration branch merely to keep topology synchronized.
- If the migration finishes before relevant `develop` divergence matters, merge the completed migration back as one deliberate product transition.
- If synchronization becomes necessary, prefer a controlled rebase/replay of migration commits onto the then-current `develop`.

## Compatibility authority

The existing Python product on the migration base is the executable reference implementation.

Compatibility is judged at observable boundaries, including as applicable:

- public CLI spelling, parsing, help semantics, stdout/stderr, and exit codes;
- environment-variable and repository/user configuration precedence;
- `.autodev/` policy/configuration semantics;
- `.autodev-run/` durable state, checkpoints, evidence, and resume behavior;
- Git/worktree/branch/commit behavior;
- GitHub CLI/API operations and classifications;
- model/runtime request shaping, privacy authorization, and role selection;
- deterministic verification, semantic verification, and repair decisions;
- queue selection, scheduler/claim decisions, and terminal states;
- packaging/install/upgrade state-preservation semantics.

Internal implementation details are not compatibility requirements unless persisted or otherwise externally observable.

## Cross-cutting migration harness

The migration should reuse the spirit of #31 and #38/#97 continuously:

1. Add a .NET 10 solution and C# test projects without switching the production launcher.
2. Build a differential harness that can feed the same fixture/scenario to the Python and C# implementations.
3. Normalize only deliberately non-contractual noise (temporary paths, timestamps where not semantically relevant, etc.).
4. Compare exit/result state, filesystem mutations, durable JSON, Git operations, mocked GitHub/provider calls, and stage decisions where relevant.
5. Run C# tests on Linux and Windows.
6. Keep existing Python tests green until the Python host is retired.

This harness is migration infrastructure, not an excuse to port Python implementation structure verbatim.

---

## Dependency-aware canonical replay order

### M00 — Migration scaffold and differential compatibility gates

**Anchor:** migration-only prerequisite, conceptually replaying the safety intent of #31 and the evaluation intent of #38/#97.

**Deliver:**

- .NET 10 solution;
- `AutoDev.Core`, `AutoDev.Cli`, and test projects;
- Linux/Windows C# CI on the migration branch;
- fixture/observation contract for Python↔C# differential tests;
- no production launcher switch;
- no claimed product capability yet.

**Depends on:** nothing.

**Why first:** every later PR gets an objective compatibility gate and a small review surface.

---

### M01 — Area Reader repository/context/verification foundation

**Historical anchor:** #12.  
**Canonicalized by:** #14, #15, and the retained Area Reader responsibilities that survived #18 and later architecture work.

**Current canonical capability:**

- repository discovery and bounded context construction;
- routing/settings;
- recommended deterministic verification groups;
- prompt/provider boundary used by Area Reader;
- persisted Area Reader outputs;
- supported standalone Area Reader workflow behavior.

**Do not replay:** obsolete benchmark-only runner shapes or retired alternate production entrypoints.

**Depends on:** M00.

---

### M02 — Explicit role taxonomy and role contracts

**Historical anchor:** #32 / PR #40.  
**Canonicalized by:** #236 and later structured-role contract hardening.

**Current canonical capability:**

`reader`, `synthesizer`, `planner`, `implementer`, `fixer`, and `verifier` are explicit role identities with typed/configurable contracts rather than a generic coder bucket.

**Depends on:** M00.

---

### M03 — Provider-neutral and runtime-neutral model execution boundary

**Historical anchor:** #46 / PR #47.  
**Canonicalized by:** #160/#161, plus current provider/runtime contracts.

**Current canonical capability:**

- provider-neutral request/result contracts;
- command/HTTP provider implementations behind a boundary;
- pluggable role runtime with OpenCode as the default;
- runtime identity participates in safe resume/invalidation;
- runtime does not own workflow stage ordering or artifact authority.

**Depends on:** M02.

---

### M04 — Model routing/configuration profiles

**Historical anchor:** #33 / PR #41.  
**Canonicalized by:** #66, #235, #238, and current configuration precedence.

**Current canonical capability:**

- named user-local model profiles;
- explicit role→model mappings;
- repository/user/explicit precedence;
- safe inspection of effective mappings;
- headless scheduler-compatible configuration;
- no silent fallback from an explicitly selected unavailable runtime/model route.

**Depends on:** M02, M03.

---

### M05 — Role-specific prompt policy

**Historical anchor:** #34 / PR #45.

**Current canonical capability:** role-specific AutoDev prompt policy remains applied at the AutoDev role boundary without depending on Codex-specific lifecycle hooks.

**Depends on:** M02, M03.

---

### M06 — Optional Headroom/context compression

**Historical anchor:** #36 / PR #59.

**Current canonical capability:** optional conservative compression at AutoDev's own provider boundary, preserving exact issue requirements/output contracts and failing open where specified.

**Depends on:** M03.

---

### M07 — Durable run manifest, checkpoints, and generic resume

**Historical anchor:** #37 / PR #60.  
**Canonicalized by:** #63, #85, #126, #321, #339 and related resume/checkpoint hardening.

**Current canonical capability:**

- versioned durable run state;
- accepted-stage checkpoints and execution identities;
- safe resume without replaying accepted work;
- explicit invalidation when execution-affecting configuration changes;
- public `resume` behavior including repeated `--invalidate-role`;
- atomic role-transition/checkpoint semantics and recovery of interrupted accepted source-editing roles;
- waiting states remain resumable rather than becoming false terminal failures.

**Depends on:** M02, M03.

---

### M08 — Independent semantic verification and bounded repair

**Historical anchor:** #35 / PR #48.  
**Canonicalized by:** #98, #137, repair-budget work, and later verification hardening.

**Current canonical capability:**

- independent verifier role;
- pass/repair/blocked semantics;
- requirements/evidence-based judgement;
- bounded semantic repair attempts;
- durable repair budget and resume behavior;
- correct ordering relative to required platform verification.

**Depends on:** M03, M07.

---

### M09 — Deterministic issue-to-PR coordinator

**Canonical anchor:** #89 / PR #90.  
**Historical lineage absorbed rather than replayed:** #49, #62, #65, #67, #78, #83, #87, #91, #93.

**Current canonical capability:**

- AutoDev, not an LLM coordinator, owns mechanical workflow transitions;
- deterministic preflight/prepare/next-action/role-acceptance/verification/repair/terminal transitions;
- bounded non-interactive role execution;
- a role process exiting zero is not completion proof;
- accepted durable artifact/state is authoritative;
- OpenCode remains a role runtime/frontend, not the workflow brain.

**Do not recreate:** the obsolete LLM-owned coordinator as an intermediate C# architecture.

**Depends on:** M02, M03, M07, M08.

---

### M10 — Local/platform verification and source identity

**Historical anchors:** #69 and #100.  
**Canonicalized by:** #95, #115, #137, #149, #215, #217, #326, #339 and current workspace/source-identity rules.

**Current canonical capability:**

- deterministic verification of the implementation actually being shipped;
- platform-aware Linux/Windows verification and deferred Windows obligations;
- Git exclusion/workspace scope semantics;
- project-native verification discovery;
- stale verification evidence invalidates safely when relevant implementation stack/source identity changes;
- exact source identity survives resume correctly.

**Depends on:** M07, M09.

---

### M11 — GitHub shipment, PR recovery, and CI truth

**Historical anchor:** #69 / PR #70.  
**Canonicalized by:** #102, #104, #105, #106, #126, #250 and current GitHub workflow modules.

**Current canonical capability:**

- commit/PR shipment bound to verified source;
- recover already-created PRs after local bookkeeping failure;
- classify successful/skipped/neutral/pending/failing CI correctly;
- bounded convergence when PR head reads lag;
- preserve actionable GitHub operation context;
- do not report ready until required CI reaches the correct terminal state;
- completed scheduler runs with an open PR remain an awaiting-merge gate.

**Depends on:** M07, M09, M10.

---

### M12 — Privacy policy, interactive consent, and durable grants

**Historical anchor:** #112 / PR #113.  
**Canonicalized by:** #128, #132, #150, #225, #240 and current runtime-neutral grant authorization.

**Current canonical capability:**

- fail-closed repository privacy policy;
- provider/runtime route evidence before prompt transmission;
- exact-route interactive consent;
- time-bounded persistent grants for unattended/headless work;
- headless execution may consume but not create consent;
- sensitive provider details are redacted from durable diagnostics;
- privacy authorization remains common/runtime-neutral even when adapters gather route-specific evidence.

**Depends on:** M03, M04, M07.

---

### M13 — User install, repository setup, configuration, doctor, and identity

**Historical anchor:** #153 / PR #176.  
**Canonicalized by:** #182/#183 documentation semantics, #208, #220, #235, #238, #260 and current installation/configuration contracts.

**Current canonical capability:**

- first-class installed `autodev` CLI;
- idempotent `repo install` and model-free `doctor`;
- repository/user configuration ownership and precedence;
- repository identity derived consistently with explicit `--owner/--repo` overrides;
- OpenCode assets optional and AutoDev-owned where installed;
- setup does not silently authorize issues or install schedulers.

**Depends on:** M03, M04, M12.

---

### M14 — Autonomous queue, management, and deterministic selection

**Historical anchors:** #151 / PR #159 and #152 / PR #175.  
**Canonicalized by:** #193, #250, #318 and current queue policy.

**Current canonical capability:**

- human-owned `autodev:managed` authorization separated from derived queue labels;
- idempotent dependency-aware reconciliation;
- deterministic `queue next` with roadmap ranking;
- existing durable run/PR gates take precedence over unrelated new work;
- explicit management command surface;
- already-satisfied completion cannot be immediately reselected.

**Depends on:** M07, M11, M13.

---

### M15 — Execution classification / manual-external boundary

**Historical anchor:** #162 / PR #174.  
**Canonicalized by:** #210, #213, #223, #227, #230 and current classifier architecture.

**Current canonical capability:**

- classify ordinary automatable work vs genuinely manual/external blockers;
- fail safe against unsupported/manual boundaries without letting weak Reader output become control-plane authority;
- classification is a deterministic/control-plane responsibility with model evidence advisory where appropriate.

**Do not recreate:** superseded Reader-owned control-plane classification.

**Depends on:** M01, M07, M09.

---

### M16 — Scheduler, worker provisioning, health, and distributed claims

**Historical anchors:** #154 / PR #177 and #156 / PR #179.  
**Canonicalized by:** #155, #232, #243, #244, #250, #264, #265, #319 and current scheduler/claim modules.

**Current canonical capability:**

- explicit per-repository scheduler installation;
- native platform registration only wakes the common dispatcher;
- dedicated worker provisioning and runtime preflight;
- health/notification state;
- distributed issue claims/leases/recovery;
- bounded heartbeat history;
- claims stop renewing when durable progress stalls;
- fresh worker clone uses the canonical repository URL;
- scheduler respects queue, privacy, resume, merge, and attention gates.

**Depends on:** M12, M13, M14, M15.

---

### M17 — SemVer intent and repository development strategy

**Historical anchors:** #246 and #266.  
**Canonicalized by:** #147/#165 language-neutral version-policy work, #273, #275 and current Git-Flow policy.

**Current canonical capability:**

- canonical `+semver` intent resolution for AutoDev-created PRs;
- trunk vs Git-Flow strategy in `.autodev/repo.json`;
- ordinary Git-Flow work targets integration branch;
- develop→main promotion derives release bump from promoted eligible intents;
- no duplicate release/develop CI semantics;
- durable runs persist effective branch strategy and reject unsafe policy drift.

**Depends on:** M11, M13.

---

### M18 — Provider-neutral Structured Output and bounded fallback

**Historical anchor:** #236.  
**Canonicalized by:** #288, #295, #307, #336.

**Current canonical capability:**

- provider-neutral structured role output contracts;
- native Structured Output where supported;
- minimal/bounded schema retries;
- safe bounded fallback text only for contracts with an explicit parser/materializer;
- AutoDev, not the model, materializes fallback artifacts;
- Reader, Synthesizer, Planner and Verifier use the generalized contract;
- schema exhaustion never silently starts unbounded retry loops.

**Depends on:** M02, M03, M07.

---

### M19 — External UX artifact resolution and role-context consumption

**Historical anchor:** #252.  
**Canonicalized by:** #253 and #262.

**Current canonical capability:**

- transport-neutral immutable UX bundle contract;
- resolver registry and content-addressed safe cache;
- OCI/GHCR/ORAS transport as a concrete adapter;
- immutable UX identity participates in durable run identity;
- selected UX context is injected into relevant roles;
- consumption evidence is persisted;
- UX content cannot override control-plane policy.

**Depends on:** M07, M09, M12, M13.

---

### M20 — Multimodal UX conformance and deterministic capture

**Historical anchor:** #268.  
**Canonicalized by:** #302, #303, #304, #305, #332.

**Current canonical capability:**

- multimodal verifier receives safe pinned visual evidence when the effective route proves image capability;
- authoritative route capability discovery;
- first-party deterministic browser capture/replay;
- first-party scoped Windows desktop capture;
- multimodal evidence is checkpoint-bound, resume-safe, and inspectable;
- browser process/profile lifecycle is deterministic and safely cleaned up.

**Depends on:** M18, M19, M10.

---

### M21 — Human-directed revision and delta replanning

**Historical anchor:** #292 / PRs #312 and #313.

**Current canonical capability:**

- `autodev revise` adopts updated issue requirements or explicit operator direction;
- current implementation remains input rather than being discarded;
- revision state is durable;
- dependent synthesis/plan/implementation/verification evidence is invalidated deliberately;
- re-entry uses Synthesizer → delta Planner → Implementer → normal verification;
- interrupted revision resumes through ordinary durable-run semantics.

**Depends on:** M07, M09, M18.

---

### M22 — First-class continuation sources

**Historical anchor:** #293 / PR #316.  
**Canonicalized by:** #342, #347, #350.

**Current canonical capability:**

- `--continue-from` accepts branch/tag/SHA and resolves once to immutable commit identity;
- continuation source is distinct from repository development/PR policy base;
- adopted HEAD remains authoritative until AutoDev creates a successor commit;
- verification/source identity binds to the active implementation parent;
- legacy continuation checkpoint identities migrate only when all safety proofs succeed.

**Depends on:** M07, M10, M17.

---

### M23 — Already-satisfied completion

**Historical anchor:** #318 / PR #324.

**Current canonical capability:**

- Planner may nominate but cannot authoritatively declare an already-satisfied outcome;
- unchanged prepared base must pass deterministic and independent semantic verification;
- confirmed no-op ends durably as `ALREADY_SATISFIED`;
- no implementation commit or PR is manufactured;
- queue/scheduler completion state prevents immediate reselection.

**Depends on:** M08, M10, M14.

---

### M24 — Notifications and interactive TUI

**Historical anchors:** #205 and #206.

**Current canonical capability:** user-visible ready/blocked/failed notifications and interactive terminal operations consume the same core state rather than implementing alternate workflow logic.

**Depends on:** M07, M14, M16.

---

### M25 — Native packaging and production-host cutover

**Historical anchors:** #122, #184, #185.  
**Canonicalized by:** #330, current installation semantics, and repository identity migration #373 where references are relevant.

**Current canonical capability after migration:**

- MSI/DEB/RPM install the C# AutoDev host instead of a bundled Python runtime;
- Linux remains a first-class supported runtime;
- package install/upgrade/uninstall preserves user/repository/run state exactly as documented;
- external dependencies such as Git/GitHub CLI and optional OpenCode/ORAS remain explicit;
- release provenance/reproducibility/attestation remains intact;
- production `autodev` launcher switches to C# only after compatibility gates for all required slices pass.

**Depends on:** all production slices required for supported AutoDev behavior.

---

## Historical work intentionally not replayed as independent C# behavior

These still matter as evidence, but should not become one migration PR each:

- accidental/no-op placeholder issues (#42–#44, #51–#58, #74, #76);
- obsolete LLM-coordinator intermediate states before #89;
- Python-only architecture mechanics from #180/#181: carry their lessons forward as C# layering/acyclicity/size tests, but do not reproduce Python module topology;
- develop↔main synchronization/promotion PRs whose only purpose was Git history alignment;
- direct hotfix sync PRs after released fixes; the product fix belongs to the capability lineage, the sync commit does not;
- docs-only wording changes unless they define an externally supported contract;
- retired benchmark/runner entrypoints superseded by canonical product entrypoints.

## C# architecture constraint

The C# host should preserve responsibility boundaries, not filenames.

Proposed project shape:

```text
src/
  AutoDev.Cli/
  AutoDev.Core/
  AutoDev.Runtimes/
  AutoDev.Git/
  AutoDev.GitHub/
  AutoDev.Platform/

tests/
  AutoDev.Core.Tests/
  AutoDev.CompatibilityTests/
  AutoDev.IntegrationTests/
```

This is intentionally provisional. A migration PR may refine project boundaries when the behavioral slice proves a better ownership split. Avoid generic dumping grounds such as a giant `Core.cs` or `Utils` namespace.

## First C# migration PR

### Proposed title

**C# migration: establish .NET host scaffold and differential compatibility gates**

### Scope

This is M00 only.

- create the .NET 10 solution and initial projects;
- add nullable/reference-analysis and warnings-as-errors appropriate for the migration;
- create a tiny C# executable entrypoint that is **not** the production `autodev` launcher yet;
- create compatibility fixture/observation primitives;
- prove a sample scenario can invoke/observe the existing Python CLI and the C# harness from the same test;
- run `dotnet test` on Linux and Windows;
- keep all existing Python CI and release behavior unchanged;
- document how later replay PRs declare their historical anchor and canonicalizing issues.

### Explicitly out of scope

- porting workflow behavior;
- changing MSI/DEB/RPM payloads;
- changing public `autodev` routing;
- deleting Python;
- adding a Python→C# or C#→Python production shim.

### Acceptance criteria

1. Migration branch builds/tests on Linux and Windows.
2. Existing Python product/tests remain unchanged and green.
3. Differential test infrastructure can compare normalized observations from two executables/adapters.
4. No production command is routed to C#.
5. The next behavioral replay (M01) can add one C# capability without redesigning the harness.

After M00, begin M01 with the current Area Reader behavior, using #12/#14/#15 as the historical review lineage.
