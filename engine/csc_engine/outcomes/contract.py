"""Runtime contract for Analysis Outcome v1 artifacts.

The JSON Schema documents the transport shape.  This module additionally
enforces cross-field rules that JSON Schema cannot express conveniently, such
as canonical reason-code ordering, summary conservation, and the
decision-table invariants between conclusion status and summary counts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any


OUTCOME_FORMAT = "analysis.outcome"
OUTCOME_VERSION = 1
DEFAULT_OUTCOME_FILENAME = "analysis.outcome.v1.json"

OUTCOME_STAGES = frozenset({"csc", "tbfv", "localization"})
COMPLETENESS_VALUES = frozenset({"complete", "partial", "unknown"})

# Canonical priority order of the v1 reason codes (AO section 3.3).  The first
# entry of a sorted list is the primary reason; known codes must appear in this
# order, followed by future unknown codes in lexicographic order.
KNOWN_REASON_CODES = (
    "source_artifact_missing",
    "source_artifact_invalid",
    "solver_timeout",
    "solver_unknown",
    "unsupported_expression",
    "missing_trace",
    "untrusted_trace",
    "compile_error",
    "analysis_error",
    "iteration_limit",
    "no_progress",
    "unresolved_obligations",
    "no_executable_tests",
    "no_applicable_scenario",
    "no_failed_verification",
    "no_localization_candidate",
)

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_REASON_INDEX = {code: index for index, code in enumerate(KNOWN_REASON_CODES)}
_ARTIFACT_CODES = frozenset({"source_artifact_missing", "source_artifact_invalid"})
_CLOSURE_CODES = frozenset({"no_applicable_scenario", "no_failed_verification"})

_STATUSES_BY_STAGE = MappingProxyType({
    "csc": frozenset({"completed", "partial", "unknown"}),
    "tbfv": frozenset({"pass", "fail", "unsupported", "unknown"}),
    "localization": frozenset({"available", "unavailable"}),
})

_OBLIGATION_KEYS = (
    "total",
    "concrete",
    "unsat",
    "ancestorStopped",
    "rangeExcluded",
    "unresolved",
)
_TBFV_COUNT_KEYS = (
    "testcases",
    "fsfUnits",
    "passed",
    "failed",
    "unknown",
    "unsupported",
    "skipped",
    "skippedScenarioUnmatched",
    "skippedMissingTrace",
    "skippedUntrustedTrace",
)


class OutcomeContractError(ValueError):
    """Raised when an Analysis Outcome document violates the v1 contract."""


def validate_outcome_document(document: Any) -> dict[str, Any]:
    """Validate and return a defensive copy of an Analysis Outcome v1 document."""
    root = _require_mapping(document, "document")
    _require_exact_keys(
        root,
        required={
            "format",
            "version",
            "jobId",
            "stage",
            "generatedAt",
            "conclusion",
            "summary",
            "sources",
        },
        optional=set(),
        field="document",
    )
    if root["format"] != OUTCOME_FORMAT:
        raise OutcomeContractError("document.format must be 'analysis.outcome'")
    if isinstance(root["version"], bool) or root["version"] != OUTCOME_VERSION:
        raise OutcomeContractError("document.version must be 1")
    job_id = _require_non_empty_string(root["jobId"], "document.jobId")
    stage = _require_non_empty_string(root["stage"], "document.stage")
    if stage not in OUTCOME_STAGES:
        raise OutcomeContractError(
            f"document.stage must be one of {sorted(OUTCOME_STAGES)}"
        )
    _require_timestamp(root["generatedAt"], "document.generatedAt")

    conclusion = _validate_conclusion(root["conclusion"], stage)

    summary = _require_mapping(root["summary"], "document.summary")
    _SUMMARY_VALIDATORS[stage](summary, "document.summary")
    _check_decision_invariants(stage, conclusion["status"], summary)
    _check_evidence_completeness(stage, conclusion, summary)

    _validate_sources(root["sources"], job_id, stage)

    _require_json_value(root, "document")
    return deepcopy(root)


def _validate_conclusion(value: Any, stage: str) -> Mapping[str, Any]:
    field = "document.conclusion"
    conclusion = _require_mapping(value, field)
    _require_exact_keys(
        conclusion,
        required={"status", "completeness", "reasonCodes"},
        optional=set(),
        field=field,
    )
    status = _require_non_empty_string(conclusion["status"], f"{field}.status")
    if status not in _STATUSES_BY_STAGE[stage]:
        raise OutcomeContractError(
            f"{field}.status must be one of {sorted(_STATUSES_BY_STAGE[stage])} "
            f"for stage '{stage}'"
        )
    completeness = _require_non_empty_string(conclusion["completeness"], f"{field}.completeness")
    if completeness not in COMPLETENESS_VALUES:
        raise OutcomeContractError(
            f"{field}.completeness must be one of {sorted(COMPLETENESS_VALUES)}"
        )
    codes = _validate_reason_codes(conclusion["reasonCodes"], f"{field}.reasonCodes")
    if completeness == "partial" and not codes:
        raise OutcomeContractError(
            f"{field}.reasonCodes must not be empty when completeness is 'partial'"
        )
    if completeness == "unknown" and not _ARTIFACT_CODES.intersection(codes):
        raise OutcomeContractError(
            f"{field}.reasonCodes must include 'source_artifact_missing' or "
            "'source_artifact_invalid' when completeness is 'unknown'"
        )
    if (
        completeness == "complete"
        and codes
        and not (len(codes) == 1 and codes[0] in _CLOSURE_CODES)
    ):
        raise OutcomeContractError(
            f"{field}.reasonCodes may only be empty, ['no_applicable_scenario'], "
            "or ['no_failed_verification'] when completeness is 'complete'"
        )
    return conclusion


def _validate_reason_codes(value: Any, field: str) -> tuple[str, ...]:
    codes = _require_sequence(value, field)
    seen: set[str] = set()
    last_known_index = -1
    last_unknown: str | None = None
    for code in codes:
        _require_non_empty_string(code, f"{field} entries")
        if not _REASON_CODE.fullmatch(code):
            raise OutcomeContractError(
                f"{field} entries must match {_REASON_CODE.pattern}"
            )
        if code in seen:
            raise OutcomeContractError(f"duplicate reason code in {field}: {code}")
        seen.add(code)
        if code in _REASON_INDEX:
            if last_unknown is not None:
                raise OutcomeContractError(
                    f"{field} must list known reason codes before unknown ones"
                )
            index = _REASON_INDEX[code]
            if index <= last_known_index:
                raise OutcomeContractError(
                    f"{field} must follow the canonical reason code order"
                )
            last_known_index = index
        else:
            if last_unknown is not None and code < last_unknown:
                raise OutcomeContractError(
                    f"{field} must sort unknown reason codes lexicographically"
                )
            last_unknown = code
    return tuple(codes)


def _validate_csc_summary(summary: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(
        summary,
        required={
            "stopReason",
            "obligations",
            "truncatedLeaves",
            "solverUnknownCalls",
            "solverTimeoutCalls",
            "executableTraceBackedTests",
        },
        optional=set(),
        field=field,
    )
    _require_non_empty_string(summary["stopReason"], f"{field}.stopReason")
    obligations_field = f"{field}.obligations"
    obligations = _require_mapping(summary["obligations"], obligations_field)
    _require_exact_keys(
        obligations,
        required=set(_OBLIGATION_KEYS),
        optional=set(),
        field=obligations_field,
    )
    counts = {
        key: _require_plain_int(
            obligations[key], f"{obligations_field}.{key}", minimum=0
        )
        for key in _OBLIGATION_KEYS
    }
    classified = sum(
        counts[key]
        for key in ("concrete", "unsat", "ancestorStopped", "rangeExcluded", "unresolved")
    )
    if counts["total"] != classified:
        raise OutcomeContractError(
            f"{obligations_field} must conserve: concrete + unsat + ancestorStopped "
            "+ rangeExcluded + unresolved == total"
        )
    for key in (
        "truncatedLeaves",
        "solverUnknownCalls",
        "solverTimeoutCalls",
        "executableTraceBackedTests",
    ):
        _require_plain_int(summary[key], f"{field}.{key}", minimum=0)


def _validate_tbfv_summary(summary: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(
        summary,
        required=set(_TBFV_COUNT_KEYS),
        optional=set(),
        field=field,
    )
    counts = {
        key: _require_plain_int(summary[key], f"{field}.{key}", minimum=0)
        for key in _TBFV_COUNT_KEYS
    }
    checks = sum(
        counts[key]
        for key in ("passed", "failed", "unknown", "unsupported", "skipped")
    )
    if checks != counts["testcases"] * counts["fsfUnits"]:
        raise OutcomeContractError(
            f"{field} must conserve: passed + failed + unknown + unsupported "
            "+ skipped == testcases * fsfUnits"
        )
    skipped_components = sum(
        counts[key]
        for key in (
            "skippedScenarioUnmatched",
            "skippedMissingTrace",
            "skippedUntrustedTrace",
        )
    )
    if counts["skipped"] != skipped_components:
        raise OutcomeContractError(
            f"{field} must conserve: skippedScenarioUnmatched + skippedMissingTrace "
            "+ skippedUntrustedTrace == skipped"
        )


def _validate_localization_summary(summary: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(
        summary,
        required={"candidateCount"},
        optional=set(),
        field=field,
    )
    _require_plain_int(summary["candidateCount"], f"{field}.candidateCount", minimum=0)


_SUMMARY_VALIDATORS: Mapping[
    str, Callable[[Mapping[str, Any], str], None]
] = MappingProxyType({
    "csc": _validate_csc_summary,
    "tbfv": _validate_tbfv_summary,
    "localization": _validate_localization_summary,
})


def _check_decision_invariants(
    stage: str,
    status: str,
    summary: Mapping[str, Any],
) -> None:
    if stage == "csc" and status == "completed":
        if summary["stopReason"] != "fixed_point":
            raise OutcomeContractError(
                "document.summary.stopReason must be 'fixed_point' "
                "when status is 'completed'"
            )
        if summary["obligations"]["unresolved"] != 0:
            raise OutcomeContractError(
                "document.summary.obligations.unresolved must be 0 "
                "when status is 'completed'"
            )
        for key in ("truncatedLeaves", "solverUnknownCalls", "solverTimeoutCalls"):
            if summary[key] != 0:
                raise OutcomeContractError(
                    f"document.summary.{key} must be 0 when status is 'completed'"
                )
    if stage == "tbfv":
        if status == "pass":
            if summary["failed"] != 0:
                raise OutcomeContractError(
                    "document.summary.failed must be 0 when status is 'pass'"
                )
            if summary["passed"] == 0:
                raise OutcomeContractError(
                    "document.summary.passed must be positive when status is 'pass'"
                )
            for key in ("unknown", "unsupported"):
                if summary[key] != 0:
                    raise OutcomeContractError(
                        f"document.summary.{key} must be 0 when status is 'pass'"
                    )
        elif status == "fail":
            if summary["failed"] == 0:
                raise OutcomeContractError(
                    "document.summary.failed must be positive when status is 'fail'"
                )
        elif status == "unsupported":
            if summary["failed"] != 0 or summary["unknown"] != 0:
                raise OutcomeContractError(
                    "document.summary.failed and .unknown must be 0 "
                    "when status is 'unsupported'"
                )
            if summary["unsupported"] == 0:
                raise OutcomeContractError(
                    "document.summary.unsupported must be positive "
                    "when status is 'unsupported'"
                )
        elif status == "unknown":
            if summary["failed"] != 0:
                raise OutcomeContractError(
                    "document.summary.failed must be 0 when status is 'unknown'"
                )
    if (
        stage == "localization"
        and status == "available"
        and summary["candidateCount"] == 0
    ):
        raise OutcomeContractError(
            "document.summary.candidateCount must be positive "
            "when status is 'available'"
        )


def _check_evidence_completeness(
    stage: str, conclusion: Mapping[str, Any], summary: Mapping[str, Any],
) -> None:
    """Enforce the frozen status/completeness combinations, not just counts.

    Unknown evidence cannot support a positive conclusion. Localization's
    upstream completeness is checked by its projector; its own summary has
    no verification counts from which to reconstruct that evidence.
    """
    status = conclusion["status"]
    completeness = conclusion["completeness"]
    codes = set(conclusion["reasonCodes"])
    if completeness == "unknown":
        expected = "unavailable" if stage == "localization" else "unknown"
        if status != expected:
            raise OutcomeContractError(
                f"status must be '{expected}' when completeness is 'unknown'"
            )
        return
    if codes & _ARTIFACT_CODES:
        raise OutcomeContractError("artifact failure reasons require completeness 'unknown'")

    if stage == "csc":
        expected = {"completed": "complete", "partial": "partial", "unknown": "unknown"}[status]
        if completeness != expected:
            raise OutcomeContractError(f"csc {status} requires completeness '{expected}'")
        if codes & _CLOSURE_CODES:
            raise OutcomeContractError("csc conclusions cannot use verification closure reasons")
    elif stage == "tbfv":
        technical_skips = summary["skippedMissingTrace"] + summary["skippedUntrustedTrace"]
        incomplete = summary["unknown"] + summary["unsupported"] + technical_skips > 0
        expected_completeness = "partial" if incomplete else "complete"
        if summary["failed"]:
            expected_status = "fail"
        elif summary["unknown"]:
            expected_status = "unknown"
        elif summary["unsupported"]:
            expected_status = "unsupported"
        elif summary["passed"]:
            expected_status = "pass"
        else:
            expected_status = "unknown"
        if (status, completeness) != (expected_status, expected_completeness):
            raise OutcomeContractError(
                "tbfv status/completeness must match the decision table: "
                f"expected {expected_status}/{expected_completeness}"
            )
        if completeness == "complete":
            expected_codes = {"no_applicable_scenario"} if status == "unknown" else set()
            if codes != expected_codes:
                raise OutcomeContractError("complete tbfv reasonCodes do not match its conclusion")
        else:
            # The summary cannot distinguish solver timeout from other unknowns;
            # accept either, and preserve support for future reason codes.
            supported = set()
            if summary["unknown"]:
                supported.update(("solver_unknown", "solver_timeout"))
            if summary["unsupported"]:
                supported.add("unsupported_expression")
            if summary["skippedMissingTrace"]:
                supported.add("missing_trace")
            if summary["skippedUntrustedTrace"]:
                supported.add("untrusted_trace")
            if (codes & set(KNOWN_REASON_CODES)) - supported:
                raise OutcomeContractError("tbfv reasonCodes contradict its summary evidence")
    elif stage == "localization":
        if completeness == "complete":
            expected_codes = {"no_failed_verification"} if status == "unavailable" else set()
            if codes != expected_codes:
                raise OutcomeContractError("complete localization reasonCodes do not match its conclusion")
        if status == "available" and codes & (_CLOSURE_CODES | {"no_localization_candidate"}):
            raise OutcomeContractError("available localization requires failure evidence and candidates")
        if "no_localization_candidate" in codes and summary["candidateCount"] != 0:
            raise OutcomeContractError("no_localization_candidate requires zero candidates")


def _validate_sources(value: Any, job_id: str, stage: str) -> None:
    field = "document.sources"
    sources = _require_sequence(value, field)
    if not sources:
        raise OutcomeContractError(f"{field} must not be empty")
    for index, entry in enumerate(sources):
        entry_field = f"{field}[{index}]"
        record = _require_mapping(entry, entry_field)
        _require_exact_keys(
            record,
            required={"jobId", "stage", "artifacts"},
            optional=set(),
            field=entry_field,
        )
        _require_non_empty_string(record["jobId"], f"{entry_field}.jobId")
        source_stage = _require_non_empty_string(record["stage"], f"{entry_field}.stage")
        if source_stage not in OUTCOME_STAGES:
            raise OutcomeContractError(
                f"{entry_field}.stage must be one of {sorted(OUTCOME_STAGES)}"
            )
        artifacts = _require_sequence(record["artifacts"], f"{entry_field}.artifacts")
        if not artifacts:
            raise OutcomeContractError(f"{entry_field}.artifacts must not be empty")
        seen: set[str] = set()
        for artifact_index, artifact in enumerate(artifacts):
            artifact_field = f"{entry_field}.artifacts[{artifact_index}]"
            path = _require_safe_artifact_path(artifact, artifact_field)
            if path in seen:
                raise OutcomeContractError(
                    f"duplicate artifact path in {entry_field}: {path}"
                )
            seen.add(path)
    if sources[0]["jobId"] != job_id:
        raise OutcomeContractError(
            f"{field}[0].jobId must reference the current job"
        )
    if sources[0]["stage"] != stage:
        raise OutcomeContractError(f"{field}[0].stage must match the current stage")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OutcomeContractError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise OutcomeContractError(f"{field} keys must be strings")
    return value


def _require_sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise OutcomeContractError(f"{field} must be an array")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    field: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise OutcomeContractError(f"{field} is missing required fields: {missing}")
    if unknown:
        raise OutcomeContractError(f"{field} has unknown fields: {unknown}")


def _require_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise OutcomeContractError(f"{field} must be a non-empty string")
    return value


def _require_plain_int(value: Any, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OutcomeContractError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise OutcomeContractError(f"{field} must be at least {minimum}")
    return value


def _require_timestamp(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutcomeContractError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OutcomeContractError(f"{field} must include a timezone")
    return text


def _require_safe_artifact_path(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OutcomeContractError(f"{field} must be a safe job-relative path")
    return text


def _require_json_value(value: Any, field: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OutcomeContractError(
            f"{field} must contain only finite JSON values"
        ) from exc
