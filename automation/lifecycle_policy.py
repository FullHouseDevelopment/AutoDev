from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REPO_CONFIG = Path(".autodev") / "repo.json"
REPO_SCHEMA = 1

PREPRODUCTION = "preproduction"
PRODUCTION = "production"
EXPERIMENTAL = "experimental"
USER_FACING = "user-facing"

SUPPORTED_LIFECYCLES = frozenset({PREPRODUCTION, PRODUCTION})
SUPPORTED_WORK_EXPOSURES = frozenset({EXPERIMENTAL, USER_FACING})

LEGACY_STRICT = "legacy-strict"
LIFECYCLE_AWARE = "lifecycle-aware"


class LifecyclePolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LifecyclePolicy:
    configured: bool
    lifecycle: str
    default_work_exposure: str
    source: str

    @property
    def mode(self) -> str:
        return LIFECYCLE_AWARE if self.configured else LEGACY_STRICT

    @property
    def work_exposure(self) -> str:
        return self.default_work_exposure

    @property
    def fingerprint(self) -> str:
        payload = {
            "mode": self.mode,
            "lifecycle": self.lifecycle,
            "work_exposure": self.work_exposure,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "configured": self.configured,
            "lifecycle": self.lifecycle,
            "work_exposure": self.work_exposure,
            "work_exposure_source": "repository-default" if self.configured else "undeclared",
            "source": self.source,
            "fingerprint": self.fingerprint,
        }


def legacy_strict_policy(*, source: str = "legacy strict default") -> LifecyclePolicy:
    return LifecyclePolicy(
        configured=False,
        lifecycle="",
        default_work_exposure="",
        source=source,
    )


def _required_choice(
    raw: Mapping[str, object],
    key: str,
    allowed: frozenset[str],
    *,
    source: str,
) -> str:
    if key not in raw:
        raise LifecyclePolicyError(f"product.{key} is required in {source}")
    value = raw.get(key)
    if not isinstance(value, str):
        raise LifecyclePolicyError(f"product.{key} in {source} must be a string")
    normalized = value.strip().casefold()
    if normalized not in allowed:
        raise LifecyclePolicyError(
            f"unsupported product.{key} in {source}: {value!r}; "
            f"expected one of {', '.join(sorted(allowed))}"
        )
    return normalized


def parse_lifecycle_policy(
    raw: object,
    *,
    source: str = ".autodev/repo.json",
) -> LifecyclePolicy:
    if raw is None:
        return legacy_strict_policy(source=f"{source}: product policy undeclared")
    if not isinstance(raw, dict):
        raise LifecyclePolicyError(f"product in {source} must be an object")

    allowed_keys = {"lifecycle", "default_work_exposure"}
    unknown = sorted(str(key) for key in raw if key not in allowed_keys)
    if unknown:
        raise LifecyclePolicyError(
            f"unsupported product field(s) in {source}: {', '.join(unknown)}"
        )

    lifecycle = _required_choice(
        raw,
        "lifecycle",
        SUPPORTED_LIFECYCLES,
        source=source,
    )
    exposure = _required_choice(
        raw,
        "default_work_exposure",
        SUPPORTED_WORK_EXPOSURES,
        source=source,
    )
    return LifecyclePolicy(
        configured=True,
        lifecycle=lifecycle,
        default_work_exposure=exposure,
        source=source,
    )


def load_lifecycle_policy(repo: Path) -> LifecyclePolicy:
    repo = repo.expanduser().resolve()
    path = repo / REPO_CONFIG
    if not path.is_file():
        return legacy_strict_policy(source=f"{path}: repository policy missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecyclePolicyError(f"invalid AutoDev repository config: {path}") from exc
    if not isinstance(value, dict):
        raise LifecyclePolicyError(f"AutoDev repository config must be a JSON object: {path}")
    if value.get("version") != REPO_SCHEMA:
        raise LifecyclePolicyError(
            f"unsupported AutoDev repository config version in {path}: {value.get('version')!r}"
        )
    return parse_lifecycle_policy(value.get("product"), source=str(path))


def state_fields(policy: LifecyclePolicy) -> dict[str, object]:
    return {
        "DeliveryPolicyMode": policy.mode,
        "LifecyclePolicyConfigured": policy.configured,
        "ProductLifecycle": policy.lifecycle,
        "DefaultWorkExposure": policy.default_work_exposure,
        "WorkExposure": policy.work_exposure,
        "WorkExposureSource": "repository-default" if policy.configured else "undeclared",
        "LifecyclePolicySource": policy.source,
        "LifecyclePolicyFingerprint": policy.fingerprint,
    }


def policy_from_state(state: Mapping[str, object]) -> LifecyclePolicy:
    mode = str(state.get("DeliveryPolicyMode", "") or "").strip()
    configured = state.get("LifecyclePolicyConfigured")
    lifecycle = str(state.get("ProductLifecycle", "") or "").strip().casefold()
    exposure = str(state.get("WorkExposure", state.get("DefaultWorkExposure", "")) or "").strip().casefold()
    source = str(state.get("LifecyclePolicySource", "") or "prepared run state").strip()

    if not mode and configured is None and not lifecycle and not exposure:
        return legacy_strict_policy(source="legacy run prepared before lifecycle policy")
    if mode == LEGACY_STRICT or configured is False:
        if lifecycle or exposure:
            raise LifecyclePolicyError(
                "prepared run marks lifecycle policy as legacy-strict but also stores lifecycle/work exposure"
            )
        return legacy_strict_policy(source=source)
    if mode not in {"", LIFECYCLE_AWARE}:
        raise LifecyclePolicyError(f"prepared run has unsupported delivery policy mode: {mode!r}")
    if configured is not True:
        raise LifecyclePolicyError("prepared run lifecycle-aware policy is missing configured=true")
    return parse_lifecycle_policy(
        {
            "lifecycle": lifecycle,
            "default_work_exposure": exposure,
        },
        source=source,
    )


def evidence_from_state(state: Mapping[str, object]) -> dict[str, object]:
    policy = policy_from_state(state)
    evidence = policy.to_json()
    persisted = str(state.get("LifecyclePolicyFingerprint", "") or "").strip()
    if persisted and persisted != policy.fingerprint:
        raise LifecyclePolicyError(
            "prepared run lifecycle policy fingerprint does not match its stored lifecycle/work exposure"
        )
    return evidence


def assert_resume_compatible(
    repo: Path,
    state: Mapping[str, object],
) -> LifecyclePolicy:
    effective = load_lifecycle_policy(repo)
    prepared = policy_from_state(state)
    persisted_fingerprint = str(state.get("LifecyclePolicyFingerprint", "") or "").strip()
    if persisted_fingerprint and persisted_fingerprint != prepared.fingerprint:
        raise LifecyclePolicyError(
            "active AutoDev run has inconsistent lifecycle policy state; its stored fingerprint no longer matches the prepared values"
        )
    if effective.fingerprint != prepared.fingerprint:
        raise LifecyclePolicyError(
            "active AutoDev run was prepared under a different delivery policy: "
            f"prepared={describe(prepared)}; current={describe(effective)}. "
            "Lifecycle/work-exposure changes can alter shipment disposition, so AutoDev will not reinterpret this run silently. "
            "Restart or explicitly re-evaluate the run under the new policy after inspecting existing work."
        )
    return effective


def assert_manifest_compatible(
    manifest: Mapping[str, object],
    state: Mapping[str, object],
) -> None:
    prepared = evidence_from_state(state)
    raw = manifest.get("delivery_policy")
    if raw is None:
        manifest_fingerprint = legacy_strict_policy(
            source="legacy manifest prepared before lifecycle policy"
        ).fingerprint
    elif isinstance(raw, dict):
        manifest_fingerprint = str(raw.get("fingerprint", "") or "").strip()
        if not manifest_fingerprint:
            manifest_fingerprint = parse_lifecycle_policy(
                (
                    {
                        "lifecycle": raw.get("lifecycle"),
                        "default_work_exposure": raw.get("work_exposure"),
                    }
                    if bool(raw.get("configured"))
                    else None
                ),
                source="run manifest delivery_policy",
            ).fingerprint
    else:
        raise LifecyclePolicyError("run manifest delivery_policy must be an object")

    if manifest_fingerprint != str(prepared["fingerprint"]):
        raise LifecyclePolicyError(
            "run manifest delivery policy does not match the prepared run state; resume requires explicit re-evaluation"
        )


def describe(policy: LifecyclePolicy) -> str:
    if not policy.configured:
        return LEGACY_STRICT
    return f"{policy.lifecycle}/{policy.work_exposure}"


def status_line(state: Mapping[str, object]) -> str:
    policy = policy_from_state(state)
    if not policy.configured:
        return "Delivery policy: legacy-strict (lifecycle/work exposure undeclared)"
    return (
        "Delivery policy: "
        f"lifecycle={policy.lifecycle} work-exposure={policy.work_exposure} "
        "source=repository-default"
    )
