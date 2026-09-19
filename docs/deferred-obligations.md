# Deferred obligations

AutoDev keeps semantic verification strict and records allowed shipment deferrals as durable obligations.

The canonical ledger is:

- `.autodev-run/deferred-obligations.json`

It deliberately lives outside `.autodev-run/current` because preparing a new run replaces the active run directory. Open obligations therefore survive later runs, PRs, and dogfood iterations.

## Provenance

Obligations keep distinct `kind` and `origin_kind` values.

Current verifier/platform paths use:

- `platform-verification` / `platform-verification`
- `semantic` / `verifier`

The shared ledger can also retain product-learning provenance for later roadmap/reconciliation work without pretending those observations were verifier defects:

- `human-dogfood`
- `runtime-telemetry`
- `new-product-requirement`
- `superseded-design-assumption`

This distinction matters for promotion policy. A verifier-derived reliability obligation and a human-discovered design pivot can both be important, but they are not the same evidence source and must not silently inherit identical semantics.

## Lifecycle

Every record has an explicit state:

- `open`
- `resolved`
- `superseded`

Source-file changes do not resolve an obligation. Resolution requires explicit reconciliation evidence, for example:

- a later semantic verifier reports the exact deferred criterion as `met`;
- the linked follow-up GitHub issue is closed and `autodev obligations reconcile` confirms it;
- an operator explicitly resolves or supersedes the obligation with a reason.

A later verifier that rediscovers the same semantic gap can reopen a previously resolved record. Explicit supersession remains durable.

## Follow-up issue policy

Repositories may opt into grouped GitHub follow-up creation:

```json
{
  "version": 1,
  "deferred_obligations": {
    "follow_up": "github-issue"
  }
}
```

Supported values are:

- `manual` — default; persist obligations but do not create GitHub issues automatically.
- `github-issue` — create one grouped follow-up issue per originating semantic verifier/run identity.

Creation is idempotent. Multiple deferred findings from the same verifier/run are grouped rather than producing one issue per finding, and the tracking issue/link is persisted back into the ledger.

Critical findings are never made deferrable merely because a follow-up issue can be created.

## CLI

Inspect open obligations:

```text
autodev obligations status
```

Reconcile linked issues that have since closed:

```text
autodev obligations reconcile
```

Explicitly resolve or supersede records:

```text
autodev obligations resolve OBLIGATION_ID --reason "verified in a later run"
autodev obligations supersede OBLIGATION_ID --reason "MVP2 replaces this design assumption"
```

Link an existing issue:

```text
autodev obligations link OBLIGATION_ID --issue 412
```

`autodev status` also reports the open obligation count, a short sample, and tracking links. Non-success reports include the same durable summary.

## Relationship to lifecycle-aware delivery

The obligation ledger does not decide shipment by itself. Semantic disposition records why a finding could defer under the effective lifecycle/work-exposure policy, while the ledger preserves the resulting debt and provenance across later runs.

Promotion toward production/user-facing exposure can query the durable ledger and reclassify unresolved obligations under the stricter target policy. Human dogfood/core-journey learning can use the same durable infrastructure while remaining distinguishable from verifier debt.
