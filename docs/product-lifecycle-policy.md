# Product lifecycle and work exposure

AutoDev can record repository-owned product lifecycle context without changing semantic verification authority.

The policy is optional and lives in `.autodev/repo.json`:

```json
{
  "version": 1,
  "product": {
    "lifecycle": "preproduction",
    "default_work_exposure": "experimental"
  }
}
```

Supported lifecycle values are:

- `preproduction`
- `production`

Supported work-exposure values are:

- `experimental`
- `user-facing`

The four combinations are intentionally independent. For example, a production repository may still contain experimental work, while a preproduction repository may expose a user-facing journey.

## Backward compatibility

Repositories with no `product` section remain in `legacy-strict` delivery-policy mode. AutoDev does not infer lifecycle from repository age, tags, issue text, branches, releases, or other heuristics.

Adding a `product` section is therefore an explicit repository policy change, not a migration that AutoDev performs automatically.

## Durable run evidence

When a run is prepared, AutoDev records the effective lifecycle policy in durable state, including:

- delivery-policy mode;
- lifecycle;
- default/effective work exposure;
- the source of the work-exposure value;
- a deterministic policy fingerprint.

The same evidence is copied into the run manifest. `status` also displays the effective lifecycle/work-exposure pair.

This makes later shipment/disposition decisions inspectable without asking the semantic verifier to reinterpret repository policy.

## Policy drift and resume

A run is interpreted under the policy that existed when the run was prepared. If `.autodev/repo.json` changes in a way that alters lifecycle/work exposure while that run is active, AutoDev refuses to silently reinterpret the run.

The run must instead be restarted or explicitly re-evaluated after the operator inspects the existing work. This is deliberately fail-closed because later lifecycle-aware disposition can change whether a verified defect blocks shipment or becomes a deferred obligation.

Legacy active runs remain resumable while the repository remains undeclared. Adding lifecycle configuration to a repository with an already-active legacy run is itself policy drift and requires re-evaluation.

## Semantic verification boundary

Lifecycle/work exposure is shipment-policy context only. It does not weaken, skip, or relabel semantic verification. The verifier continues to report whether the implementation satisfies the task and its acceptance criteria. Follow-on delivery/disposition logic may use lifecycle context when deciding what to do with verifier findings, but the verifier's findings remain intact.

## Run-level overrides

Issue #355 does not introduce run-level lifecycle/work-exposure overrides. Repository policy is the only source for lifecycle-aware mode in this implementation. If bounded overrides are added later, they must be explicit, persisted in durable state and the run manifest, visible in status, and subject to resume-drift checks rather than inferred implicitly.
