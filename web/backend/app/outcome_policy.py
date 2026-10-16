"""Single authoritative policy for downstream stage availability.

The policy consumes only contract-validated Analysis Outcome documents (it
never re-reads the raw facts) and drives both the read-only ``nextStages``
view of the outcome API and the start-time gates of ``CoreAdapter``.  A
missing or invalid upstream outcome fails closed at the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from csc_engine.outcomes import KNOWN_REASON_CODES


DOWNSTREAM_BY_STAGE: Mapping[str, str | None] = MappingProxyType({
    "csc": "tbfv",
    "tbfv": "localization",
    "localization": None,
})

_REASON_INDEX = {code: index for index, code in enumerate(KNOWN_REASON_CODES)}


@dataclass(frozen=True)
class PolicyDecision:
    available: bool
    reason_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()


def evaluate_next_stage(stage: str, outcome: Mapping[str, Any]) -> PolicyDecision:
    """Decide whether the stage after ``stage`` may start from ``outcome``."""
    rule = _RULES.get(stage)
    if rule is None:
        raise ValueError(f"stage has no downstream policy: {stage!r}")
    return rule(outcome)


def next_stages(stage: str, outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the read-only ``nextStages`` view for an outcome document."""
    if stage not in DOWNSTREAM_BY_STAGE:
        raise ValueError(f"stage has no downstream policy: {stage!r}")
    downstream = DOWNSTREAM_BY_STAGE[stage]
    if downstream is None:
        return []
    decision = evaluate_next_stage(stage, outcome)
    return [
        {
            "stage": downstream,
            "available": decision.available,
            "reasonCodes": list(decision.reason_codes),
            "warningCodes": list(decision.warning_codes),
        }
    ]


def _tbfv_rule(outcome: Mapping[str, Any]) -> PolicyDecision:
    conclusion = outcome["conclusion"]
    status = conclusion["status"]
    if status == "completed":
        return PolicyDecision(True)
    if status == "partial":
        if outcome["summary"]["executableTraceBackedTests"] > 0:
            return PolicyDecision(True, warning_codes=("upstream_partial",))
        return PolicyDecision(
            False,
            reason_codes=_canonical((*conclusion["reasonCodes"], "no_executable_tests")),
        )
    # The contract keeps csc statuses to completed/partial/unknown, so this
    # covers the remaining unknown case with the upstream reasons.
    return PolicyDecision(False, reason_codes=_canonical(conclusion["reasonCodes"]))


def _localization_rule(outcome: Mapping[str, Any]) -> PolicyDecision:
    conclusion = outcome["conclusion"]
    if conclusion["status"] != "fail":
        return PolicyDecision(False, reason_codes=("no_failed_verification",))
    if conclusion["completeness"] == "partial":
        return PolicyDecision(True, warning_codes=("incomplete_evidence",))
    return PolicyDecision(True)


def _canonical(codes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    unique = set(codes)
    return tuple(
        sorted(
            unique,
            key=lambda code: (_REASON_INDEX.get(code, len(_REASON_INDEX)), code),
        )
    )


_RULES: Mapping[str, Callable[[Mapping[str, Any]], PolicyDecision]] = MappingProxyType({
    "csc": _tbfv_rule,
    "tbfv": _localization_rule,
})
