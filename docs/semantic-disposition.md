# Semantic shipment disposition

AutoDev separates semantic diagnosis from shipment policy.

The Semantic Verifier still answers **what is missing or wrong**. Its durable `verification-result.json`, verdict, findings, requirements, and repair brief are not rewritten when a finding is deferred.

The coordinator-owned disposition layer answers **whether the current finding must block shipment under the effective lifecycle/work-exposure policy**.

## Durable artifact

After semantic evaluation AutoDev writes `.autodev-run/current/semantic-disposition.json`. It records:

- the SHA-256 identity and original verdict of `verification-result.json`;
- the lifecycle/work-exposure policy fingerprint from the prepared run;
- a stable entry identity for each verifier finding and unmet/uncertain acceptance criterion;
- the coordinator disposition for each entry;
- human acceptance metadata when an override is used.

The verifier artifact remains unchanged.

## Disposition states

AutoDev distinguishes:

- `CLEAN_PASS` — the verifier passed without deferred shipment obligations;
- `AWAITING_REPAIR` — normal semantic repair remains appropriate;
- `AWAITING_HUMAN_DISPOSITION` — a non-critical semantic blocker may only be deferred explicitly by a human;
- `ACCEPTED_WITH_DEFERRALS` — shipment may continue, but the original semantic gaps remain durable and visible;
- `HARD_BLOCK` — a critical-by-default or legacy-strict blocker may not use the broad deferral mechanism.

`ACCEPTED_WITH_DEFERRALS` is not a synonym for `pass`. `LastSemanticVerdict` remains the verifier's original value and the semantic checkpoint records both the original verdict and the separate disposition.

## Critical-by-default safety boundary

Broad deferral does not override findings that credibly indicate:

- destructive or silent data loss/corruption;
- unsafe/destructive migrations;
- security/privacy boundary violations or authorization/authentication bypass;
- secrets/credential exposure;
- dangerous external side effects;
- inability to exercise the declared core journey;
- misleading durability evidence, such as an action represented as persisted when it is not durable.

The current verifier contract contains free-text findings rather than a trusted deterministic safety taxonomy. AutoDev therefore uses only narrow high-signal critical detection for the broad override and never treats the absence of a critical keyword as permission to auto-defer a blocking finding. Non-critical **blocking** findings require an explicit human disposition.

Warnings are the only finding class eligible for policy-only deferral in a lifecycle-aware repository. This deliberately conservative boundary can become richer later if a versioned, trustworthy finding taxonomy is introduced.

## Human acceptance

A lifecycle-aware repository can explicitly accept current non-critical blockers and continue:

```bash
autodev resume --accept-with-deferrals --deferral-reason "Useful owner dogfood build; track remaining presentation gaps separately"
```

A reason is mandatory when the command overrides blocking findings or unmet acceptance criteria. The audit record includes the exact verifier hash, policy fingerprint, run/issue identity, timestamp, reason, and deferred entry IDs.

Legacy-strict repositories cannot use this broad override. They preserve the pre-lifecycle behavior until lifecycle/work exposure is explicitly configured.

## Resume and idempotency

Acceptance is valid only for the exact verifier result and exact prepared lifecycle policy. If either identity changes, the old acceptance no longer satisfies the semantic shipment gate.

Repeated acceptance of the same already-accepted verifier/policy identity is idempotent: AutoDev preserves the original audit record instead of appending duplicate approvals.

When acceptance is valid, the semantic checkpoint is completed with both `verification-result.json` and `semantic-disposition.json` as durable artifacts. PR/CI and ready proof may then continue using the disposition gate while keeping the verifier verdict untouched.

## Repair-budget exhaustion

A repair-budget exhaustion no longer has to be the final interpretation of the semantic result. The disposition layer evaluates the preserved verifier result at the semantic boundary. Critical/legacy findings remain blocked; non-critical lifecycle-aware blockers can be explicitly accepted with deferrals and then resume from the completed semantic checkpoint rather than manually editing run state.
