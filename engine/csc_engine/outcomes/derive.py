"""Deterministic projection of stage facts into Analysis Outcome v1 documents.

The projector is a pure function over a stage result directory: it reads the
authoritative fact artifacts (AO section 4), classifies them with the stage
decision tables (AO sections 5-7), and returns a transport document for the
runtime contract.  Missing or damaged facts never raise; they fail closed into
an ``unknown`` conclusion so a succeeded subprocess still records a conclusion
consumers can trust.  Misuse (unknown stage, inconsistent upstream wiring)
raises ``OutcomeProjectionError``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..sbfl import SBFLContractError, validate_sbfl_report

from .contract import (
    DEFAULT_OUTCOME_FILENAME,
    KNOWN_REASON_CODES,
    OUTCOME_FORMAT,
    OUTCOME_VERSION,
    OutcomeContractError,
    validate_outcome_document,
)


CSC_OBLIGATIONS_FILENAME = "csc.obligations.v1.json"
CSC_TESTCASES_FILENAME = "testcases.json"
CSC_COVERAGE_FILENAME = "coverage.facts.v1.json"
SOLVER_TELEMETRY_FILENAME = "solver.telemetry.v1.json"
TBFV_REPORT_FILENAME = "refined_tbfv_report.json"
SBFL_LOCALIZATION_FILENAME = "sbfl.localization.v1.json"

_TRACE_BACKED_STATUSES = frozenset(
    {"bootstrap", "sat", "batch_safe", "counterexample"}
)
_OBLIGATION_STATUSES = frozenset(
    {"covered", "uncovered", "infeasible", "rangeExcluded"}
)
_CSC_STOP_REASONS = frozenset(
    {"fixed_point", "iteration_limit", "no_progress", "compile_error", "analysis_error"}
)
_TBFV_STATUSES = frozenset({"pass", "fail", "skipped", "unsupported", "unknown"})
_SCENARIO_UNMATCHED_REASON = "scenario_unmatched"
_MISSING_TRACE_REASON = "missing_trace_path"
_UNTRUSTED_TRACE_PREFIX = "trace_status="
_SOLVER_TIMEOUT_REASON = "solver_timeout"
_SOLVER_UNKNOWN_REASON = "solver_unknown"

_REASON_INDEX = {code: index for index, code in enumerate(KNOWN_REASON_CODES)}


class OutcomeProjectionError(ValueError):
    """Raised for internal projector misuse rather than fact failures."""


class _FactError(Exception):
    """Missing or damaged fact artifact; carries the fail-closed reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def project_outcome(
    stage: str,
    *,
    job_id: str,
    generated_at: str,
    source_root: str | Path,
    upstream_job_id: str | None = None,
    upstream_result_root: str | Path | None = None,
) -> dict[str, Any]:
    """Project the stage facts under ``source_root`` into an outcome document."""
    projector = _PROJECTORS.get(stage)
    if projector is None:
        raise OutcomeProjectionError(f"unsupported outcome stage: {stage!r}")
    root = Path(source_root)
    try:
        conclusion, summary, sources = projector(
            root, job_id, upstream_job_id, upstream_result_root
        )
    except _FactError as error:
        conclusion = {
            "status": _fail_closed_statuses[stage],
            "completeness": "unknown",
            "reasonCodes": [error.code],
        }
        summary = _empty_summaries[stage]()
        sources = _failure_sources[stage](job_id, root, upstream_job_id)
    return {
        "format": OUTCOME_FORMAT,
        "version": OUTCOME_VERSION,
        "jobId": job_id,
        "stage": stage,
        "generatedAt": generated_at,
        "conclusion": conclusion,
        "summary": summary,
        "sources": sources,
    }


# --------------------------------------------------------------------------- #
# CSC
# --------------------------------------------------------------------------- #


def _project_csc(
    root: Path,
    job_id: str,
    upstream_job_id: str | None,
    upstream_result_root: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    (
        stop_reason,
        covered,
        uncovered,
        infeasible,
        range_excluded,
        truncated,
        obligations,
    ) = _validate_obligation_facts(_read_json(root, CSC_OBLIGATIONS_FILENAME))

    testcases = _read_json(root, CSC_TESTCASES_FILENAME)
    _require(isinstance(testcases, list))
    executable = _executable_trace_backed_tests(testcases, root)

    segment_unknown, segment_timeouts = _read_solver_segment(root, "csc")

    coverage_path = root / CSC_COVERAGE_FILENAME
    coverage_complete: bool | None = None
    if coverage_path.is_file():
        coverage_complete = _validate_coverage_facts(
            _read_json(root, CSC_COVERAGE_FILENAME)
        )

    # A legacy X marker alone is ambiguous. Only per-obligation provenance may
    # promote it to a proved UNSAT result; old facts remain fail-closed.
    unsat = sum(
        (obligation.get("resolution") or {}).get("kind") == "solver_unsat"
        for obligation in obligations
    )
    ancestor_stopped = sum(
        (obligation.get("resolution") or {}).get("kind") == "ancestor_stopped"
        for obligation in obligations
    )
    proven_range_excluded = sum(
        (obligation.get("resolution") or {}).get("kind") == "range_excluded"
        for obligation in obligations
    )
    unresolved = (uncovered + infeasible + range_excluded
                  - unsat - ancestor_stopped - proven_range_excluded)
    completed = (
        stop_reason == "fixed_point"
        and segment_unknown == 0
        and segment_timeouts == 0
        and unresolved == 0
        and truncated == 0
        and coverage_complete is not False
    )
    if completed:
        conclusion = {
            "status": "completed",
            "completeness": "complete",
            "reasonCodes": [],
        }
    else:
        codes: set[str] = set()
        if stop_reason != "fixed_point":
            codes.add(stop_reason)
        if segment_unknown > 0:
            codes.add("solver_unknown")
        if segment_timeouts > 0:
            codes.add("solver_timeout")
        if unresolved > 0 or truncated > 0:
            codes.add("unresolved_obligations")
        if coverage_complete is False:
            codes.add("missing_trace")
        conclusion = {
            "status": "partial",
            "completeness": "partial",
            "reasonCodes": _canonical(codes),
        }

    summary = {
        "stopReason": stop_reason,
        "obligations": {
            "total": len(obligations),
            "concrete": covered,
            "unsat": unsat,
            "ancestorStopped": ancestor_stopped,
            "rangeExcluded": proven_range_excluded,
            "unresolved": unresolved,
        },
        "truncatedLeaves": truncated,
        "solverUnknownCalls": segment_unknown,
        "solverTimeoutCalls": segment_timeouts,
        "executableTraceBackedTests": executable,
    }
    artifacts = [
        CSC_OBLIGATIONS_FILENAME,
        CSC_TESTCASES_FILENAME,
        SOLVER_TELEMETRY_FILENAME,
    ]
    if coverage_path.is_file():
        artifacts.append(CSC_COVERAGE_FILENAME)
    sources = [{"jobId": job_id, "stage": "csc", "artifacts": artifacts}]
    return conclusion, summary, sources


def _validate_obligation_facts(
    facts: Any,
) -> tuple[str, int, int, int, int, int, list[Any]]:
    _require(isinstance(facts, Mapping))
    _require(facts.get("format") == "csc.obligations")
    _require(_is_version_one(facts.get("version")))
    _require(isinstance(facts.get("complete"), bool))
    stop_reason = facts.get("stopReason")
    _require(isinstance(stop_reason, str) and stop_reason in _CSC_STOP_REASONS)
    counts = facts.get("counts")
    _require(isinstance(counts, Mapping))
    covered = _count(counts.get("covered"))
    uncovered = _count(counts.get("uncovered"))
    infeasible = _count(counts.get("infeasible"))
    range_excluded = _count(counts.get("rangeExcluded"))
    truncated = _count(counts.get("truncated"))
    obligations = facts.get("obligations")
    _require(isinstance(obligations, list))
    _require(
        covered + uncovered + infeasible + range_excluded == len(obligations)
    )
    for obligation in obligations:
        _require(isinstance(obligation, Mapping))
        status = obligation.get("status")
        _require(isinstance(status, str) and status in _OBLIGATION_STATUSES)
        resolution = obligation.get("resolution")
        if resolution is not None:
            _require(isinstance(resolution, Mapping))
            kind = resolution.get("kind")
            if kind == "solver_unsat":
                _require(status == "infeasible")
                _require(resolution.get("solverResult") == "unsat")
                _require(resolution.get("scope") == "java_values")
            elif kind == "ancestor_stopped":
                _require(status == "uncovered")
            elif kind in {"solver_unknown", "solver_error"}:
                _require(status == "uncovered")
                _require(resolution.get("solverResult") == (
                    "unknown" if kind == "solver_unknown" else "error"))
            elif kind == "range_excluded":
                _require(status == "rangeExcluded")
                _require(resolution.get("boundedResult") == "unsat")
                _require(resolution.get("globalResult") == "sat")
            else:
                _require(False)
    actual = Counter(obligation["status"] for obligation in obligations)
    _require(all(actual[status] == counts[status] for status in _OBLIGATION_STATUSES))
    return (
        stop_reason,
        covered,
        uncovered,
        infeasible,
        range_excluded,
        truncated,
        obligations,
    )


def _validate_coverage_facts(facts: Any) -> bool:
    _require(isinstance(facts, Mapping))
    _require(facts.get("format") == "coverage.facts")
    _require(_is_version_one(facts.get("version")))
    complete = facts.get("complete")
    _require(isinstance(complete, bool))
    _count(facts.get("expectedTraceCount"))
    _count(facts.get("traceCount"))
    return complete


def _executable_trace_backed_tests(testcases: list[Any], root: Path) -> int:
    resolved_root = root.resolve()
    executable = 0
    for record in testcases:
        _require(isinstance(record, Mapping))
        status = record.get("status")
        if not isinstance(status, str) or status not in _TRACE_BACKED_STATUSES:
            continue
        trace_path = record.get("trace_path")
        if not isinstance(trace_path, str) or not trace_path:
            continue
        candidate = Path(trace_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if resolved_root in resolved.parents and resolved.is_file():
            executable += 1
    return executable


# --------------------------------------------------------------------------- #
# TBFV
# --------------------------------------------------------------------------- #


def _project_tbfv(
    root: Path,
    job_id: str,
    upstream_job_id: str | None,
    upstream_result_root: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    report = _read_json(root, TBFV_REPORT_FILENAME)
    (
        counts,
        results,
        scenario_unmatched,
        skipped_missing,
        skipped_untrusted,
        unknown_timeout,
        unknown_solver,
    ) = _validate_tbfv_report(report)

    segment_unknown, segment_timeouts = _read_solver_segment(root, "tbfv")
    _require(segment_unknown == counts["unknown"])
    _require(
        segment_timeouts
        == sum(1 for record in results if record.get("reason") == _SOLVER_TIMEOUT_REASON)
    )

    failed = counts["failed"]
    unknown = counts["unknown"]
    unsupported = counts["unsupported"]
    passed = counts["passed"]
    tech_skipped = skipped_missing + skipped_untrusted
    applicable = len(results) - scenario_unmatched

    solver_codes: set[str] = set()
    if unknown_timeout > 0:
        solver_codes.add("solver_timeout")
    if unknown_solver > 0:
        solver_codes.add("solver_unknown")
    trace_codes: set[str] = set()
    if skipped_missing > 0:
        trace_codes.add("missing_trace")
    if skipped_untrusted > 0:
        trace_codes.add("untrusted_trace")

    if failed > 0 and unknown == 0 and unsupported == 0 and tech_skipped == 0:
        conclusion = {"status": "fail", "completeness": "complete", "reasonCodes": []}
    elif failed > 0:
        codes = solver_codes | trace_codes
        if unsupported > 0:
            codes.add("unsupported_expression")
        conclusion = {
            "status": "fail",
            "completeness": "partial",
            "reasonCodes": _canonical(codes),
        }
    elif unknown > 0:
        conclusion = {
            "status": "unknown",
            "completeness": "partial",
            "reasonCodes": _canonical(solver_codes),
        }
    elif unsupported > 0:
        conclusion = {
            "status": "unsupported",
            "completeness": "partial",
            "reasonCodes": ["unsupported_expression"],
        }
    elif passed > 0 and tech_skipped > 0:
        conclusion = {
            "status": "pass",
            "completeness": "partial",
            "reasonCodes": _canonical(trace_codes),
        }
    elif passed > 0:
        conclusion = {"status": "pass", "completeness": "complete", "reasonCodes": []}
    elif applicable > 0:
        conclusion = {
            "status": "unknown",
            "completeness": "partial",
            "reasonCodes": _canonical(trace_codes),
        }
    else:
        conclusion = {
            "status": "unknown",
            "completeness": "complete",
            "reasonCodes": ["no_applicable_scenario"],
        }

    summary = {
        "testcases": counts["testcases"],
        "fsfUnits": counts["fsf_units"],
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "unsupported": unsupported,
        "skipped": counts["skipped"],
        "skippedScenarioUnmatched": scenario_unmatched,
        "skippedMissingTrace": skipped_missing,
        "skippedUntrustedTrace": skipped_untrusted,
    }
    sources = [
        {
            "jobId": job_id,
            "stage": "tbfv",
            "artifacts": [TBFV_REPORT_FILENAME, SOLVER_TELEMETRY_FILENAME],
        }
    ]
    return conclusion, summary, sources


def _validate_tbfv_report(
    report: Any,
) -> tuple[dict[str, int], list[Any], int, int, int, int, int]:
    _require(isinstance(report, Mapping))
    summary = report.get("summary")
    _require(isinstance(summary, Mapping))
    counts = {
        key: _count(summary.get(key))
        for key in (
            "testcases",
            "fsf_units",
            "passed",
            "failed",
            "unknown",
            "unsupported",
            "skipped",
        )
    }
    results = report.get("results")
    _require(isinstance(results, list))
    checked = sum(
        counts[key]
        for key in ("passed", "failed", "unknown", "unsupported", "skipped")
    )
    _require(checked == counts["testcases"] * counts["fsf_units"])
    _require(checked == len(results))

    scenario_unmatched = 0
    skipped_missing = 0
    skipped_untrusted = 0
    unknown_timeout = 0
    unknown_solver = 0
    for record in results:
        _require(isinstance(record, Mapping))
        status = record.get("status")
        _require(isinstance(status, str) and status in _TBFV_STATUSES)
        reason = record.get("reason")
        if status == "skipped":
            if reason == _SCENARIO_UNMATCHED_REASON:
                scenario_unmatched += 1
            elif reason == _MISSING_TRACE_REASON:
                skipped_missing += 1
            elif isinstance(reason, str) and reason.startswith(
                _UNTRUSTED_TRACE_PREFIX
            ):
                skipped_untrusted += 1
            else:
                raise _FactError("source_artifact_invalid")
        elif status == "unknown":
            if reason == _SOLVER_TIMEOUT_REASON:
                unknown_timeout += 1
            elif reason == _SOLVER_UNKNOWN_REASON:
                unknown_solver += 1
            else:
                raise _FactError("source_artifact_invalid")
    _require(
        counts["skipped"] == scenario_unmatched + skipped_missing + skipped_untrusted
    )
    actual = Counter(record["status"] for record in results)
    for status, key in (
        ("pass", "passed"), ("fail", "failed"), ("unknown", "unknown"),
        ("unsupported", "unsupported"), ("skipped", "skipped"),
    ):
        _require(actual[status] == counts[key])
    return (
        counts,
        results,
        scenario_unmatched,
        skipped_missing,
        skipped_untrusted,
        unknown_timeout,
        unknown_solver,
    )


# --------------------------------------------------------------------------- #
# Localization
# --------------------------------------------------------------------------- #


def _project_localization(
    root: Path,
    job_id: str,
    upstream_job_id: str | None,
    upstream_result_root: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if upstream_result_root is None:
        raise _FactError("source_artifact_missing")
    if upstream_job_id is None:
        raise OutcomeProjectionError(
            "localization projection requires the upstream job id with its root"
        )
    upstream = _read_upstream_outcome(upstream_result_root)
    _require(upstream["stage"] == "tbfv")
    _require(upstream["jobId"] == upstream_job_id)

    try:
        sbfl = validate_sbfl_report(_read_json(root, SBFL_LOCALIZATION_FILENAME))
    except SBFLContractError as exc:
        raise _FactError("source_artifact_invalid") from exc
    ranking = sbfl["ranking"]
    totals = sbfl["totals"]
    candidate_count = _count(totals.get("candidateLines"))
    _require(candidate_count == len(ranking))

    upstream_conclusion = upstream["conclusion"]
    upstream_status = upstream_conclusion["status"]
    upstream_completeness = upstream_conclusion["completeness"]
    upstream_codes = list(upstream_conclusion["reasonCodes"])
    upstream_failed = upstream["summary"]["failed"]

    if upstream_status == "fail" and candidate_count > 0:
        conclusion = {
            "status": "available",
            "completeness": upstream_completeness,
            "reasonCodes": upstream_codes,
        }
    elif upstream_status == "fail":
        conclusion = {
            "status": "unavailable",
            "completeness": "partial",
            "reasonCodes": ["no_localization_candidate"],
        }
    elif upstream_failed == 0 and upstream_completeness == "complete":
        conclusion = {
            "status": "unavailable",
            "completeness": "complete",
            "reasonCodes": ["no_failed_verification"],
        }
    elif upstream_failed == 0 and upstream_completeness == "partial":
        conclusion = {
            "status": "unavailable",
            "completeness": "partial",
            "reasonCodes": ["no_failed_verification"],
        }
    else:
        # The upstream document is valid but its evidence is unusable; this is
        # the "upstream evidence unavailable" case of the status definition.
        conclusion = {
            "status": "unavailable",
            "completeness": "unknown",
            "reasonCodes": upstream_codes,
        }

    summary = {"candidateCount": candidate_count}
    sources = [
        {
            "jobId": job_id,
            "stage": "localization",
            "artifacts": [SBFL_LOCALIZATION_FILENAME],
        },
        {
            "jobId": upstream_job_id,
            "stage": "tbfv",
            "artifacts": [DEFAULT_OUTCOME_FILENAME],
        },
    ]
    return conclusion, summary, sources


def _read_upstream_outcome(upstream_result_root: str | Path) -> dict[str, Any]:
    path = Path(upstream_result_root) / DEFAULT_OUTCOME_FILENAME
    if not path.is_file():
        raise _FactError("source_artifact_missing")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _FactError("source_artifact_invalid") from exc
    try:
        return validate_outcome_document(decoded)
    except OutcomeContractError as exc:
        raise _FactError("source_artifact_invalid") from exc


# --------------------------------------------------------------------------- #
# Fail-closed helpers
# --------------------------------------------------------------------------- #


def _empty_csc_summary() -> dict[str, Any]:
    return {
        "stopReason": "unknown",
        "obligations": {
            "total": 0,
            "concrete": 0,
            "unsat": 0,
            "ancestorStopped": 0,
            "rangeExcluded": 0,
            "unresolved": 0,
        },
        "truncatedLeaves": 0,
        "solverUnknownCalls": 0,
        "solverTimeoutCalls": 0,
        "executableTraceBackedTests": 0,
    }


def _empty_tbfv_summary() -> dict[str, Any]:
    return {
        "testcases": 0,
        "fsfUnits": 0,
        "passed": 0,
        "failed": 0,
        "unknown": 0,
        "unsupported": 0,
        "skipped": 0,
        "skippedScenarioUnmatched": 0,
        "skippedMissingTrace": 0,
        "skippedUntrustedTrace": 0,
    }


def _empty_localization_summary() -> dict[str, Any]:
    return {"candidateCount": 0}


_empty_summaries: Mapping[str, Callable[[], dict[str, Any]]] = MappingProxyType({
    "csc": _empty_csc_summary,
    "tbfv": _empty_tbfv_summary,
    "localization": _empty_localization_summary,
})

_fail_closed_statuses: Mapping[str, str] = MappingProxyType({
    "csc": "unknown",
    "tbfv": "unknown",
    "localization": "unavailable",
})


def _csc_failure_sources(
    job_id: str, root: Path, upstream_job_id: str | None
) -> list[dict[str, Any]]:
    artifacts = [
        CSC_OBLIGATIONS_FILENAME,
        CSC_TESTCASES_FILENAME,
        SOLVER_TELEMETRY_FILENAME,
    ]
    if (root / CSC_COVERAGE_FILENAME).is_file():
        artifacts.append(CSC_COVERAGE_FILENAME)
    return [{"jobId": job_id, "stage": "csc", "artifacts": artifacts}]


def _tbfv_failure_sources(
    job_id: str, root: Path, upstream_job_id: str | None
) -> list[dict[str, Any]]:
    return [
        {
            "jobId": job_id,
            "stage": "tbfv",
            "artifacts": [TBFV_REPORT_FILENAME, SOLVER_TELEMETRY_FILENAME],
        }
    ]


def _localization_failure_sources(
    job_id: str, root: Path, upstream_job_id: str | None
) -> list[dict[str, Any]]:
    sources = [
        {
            "jobId": job_id,
            "stage": "localization",
            "artifacts": [SBFL_LOCALIZATION_FILENAME],
        }
    ]
    if upstream_job_id is not None:
        sources.append(
            {
                "jobId": upstream_job_id,
                "stage": "tbfv",
                "artifacts": [DEFAULT_OUTCOME_FILENAME],
            }
        )
    return sources


_failure_sources: Mapping[
    str, Callable[[str, Path, str | None], list[dict[str, Any]]]
] = MappingProxyType({
    "csc": _csc_failure_sources,
    "tbfv": _tbfv_failure_sources,
    "localization": _localization_failure_sources,
})


# --------------------------------------------------------------------------- #
# Shared fact helpers
# --------------------------------------------------------------------------- #


def _read_json(root: Path, name: str) -> Any:
    path = root / name
    if not path.is_file():
        raise _FactError("source_artifact_missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _FactError("source_artifact_invalid") from exc


def _read_solver_segment(root: Path, stage: str) -> tuple[int, int]:
    artifact = _read_json(root, SOLVER_TELEMETRY_FILENAME)
    _require(isinstance(artifact, Mapping))
    _require(artifact.get("format") == "solver.telemetry")
    _require(_is_version_one(artifact.get("version")))
    segments = artifact.get("segments")
    _require(isinstance(segments, Mapping))
    segment = segments.get(stage)
    _require(isinstance(segment, Mapping))
    calls = _count(segment.get("calls"))
    sat = _count(segment.get("sat"))
    unsat = _count(segment.get("unsat"))
    unknown = _count(segment.get("unknown"))
    timeouts = _count(segment.get("timeouts"))
    _require(calls == sat + unsat + unknown)
    _require(timeouts <= unknown)
    return unknown, timeouts


def _canonical(codes: set[str]) -> list[str]:
    return sorted(codes, key=_REASON_INDEX.__getitem__)


def _require(condition: bool) -> None:
    if not condition:
        raise _FactError("source_artifact_invalid")


def _is_version_one(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _FactError("source_artifact_invalid")
    return value


_PROJECTORS: Mapping[
    str,
    Callable[
        [Path, str, str | None, str | Path | None],
        tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]],
    ],
] = MappingProxyType({
    "csc": _project_csc,
    "tbfv": _project_tbfv,
    "localization": _project_localization,
})
