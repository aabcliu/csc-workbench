"""Builders and validators for metric source facts produced by CSC generation."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .cct import CCT, INFEASIBLE_MARKER, RANGE_EXCLUDED_MARKER
from .execution_trace import ExecutionEvent, parse_trace_jsonl


def build_coverage_facts(
    catalog: dict[str, Any],
    trace_paths: Iterable[str | Path],
    *,
    expected_trace_count: int | None = None,
) -> dict:
    decisions = _catalog_entries(catalog, "decisions")
    conditions = _catalog_entries(catalog, "conditions")
    exclusions = catalog.get("exclusions", [])
    if not isinstance(exclusions, list) or any(not isinstance(item, dict) for item in exclusions):
        raise ValueError("coverage catalog exclusions must be an array of objects")
    branch_hits = _empty_hits(decisions)
    condition_hits = _empty_hits(conditions)
    paths = list(trace_paths)
    if expected_trace_count is None:
        expected_trace_count = len(paths)
    if (
        isinstance(expected_trace_count, bool)
        or not isinstance(expected_trace_count, int)
        or expected_trace_count < 0
    ):
        raise ValueError("expected_trace_count must be a non-negative integer")
    trace_count = 0
    for path in paths:
        trace_path = Path(path)
        if not trace_path.is_file():
            continue
        trace_count += 1
        for event in parse_trace_jsonl(trace_path):
            _record_coverage_event(event, branch_hits, condition_hits)
    return {
        "format": "coverage.facts",
        "version": 1,
        "complete": trace_count == expected_trace_count,
        "expectedTraceCount": expected_trace_count,
        "traceCount": trace_count,
        "decisions": decisions,
        "conditions": conditions,
        "exclusions": [dict(item) for item in exclusions],
        "branchHits": branch_hits,
        "conditionHits": condition_hits,
    }


def build_obligation_facts(cct: CCT, stop_reason: str) -> dict:
    counts = {
        "covered": 0,
        "uncovered": 0,
        "infeasible": 0,
        "rangeExcluded": 0,
        "truncated": cct.collect_stats()["truncated_leaves"],
    }
    obligations: list[dict[str, Any]] = []

    def visit(node, path: str) -> None:
        if node is None or node.is_leaf:
            return
        for result, side, child in ((False, "false", node.left), (True, "true", node.right)):
            status = _obligation_status(child)
            counts[status] += 1
            obligation = {
                "id": f"{path or 'root'}:{side}",
                "line": node.condition.line_number,
                "condition": node.condition.condition_string,
                "loopCount": node.condition.loop_count,
                "outcome": result,
                "status": status,
            }
            resolution = (
                getattr(child, "terminal_resolution", None)
                if child is not None
                else getattr(node, "branch_resolutions", {}).get(result)
            )
            if resolution is not None:
                obligation["resolution"] = dict(resolution)
            obligations.append(obligation)
        visit(node.left, f"{path}F")
        visit(node.right, f"{path}T")

    visit(cct.root, "")
    return {
        "format": "csc.obligations",
        "version": 1,
        "complete": stop_reason == "fixed_point",
        "stopReason": stop_reason,
        "counts": counts,
        "obligations": obligations,
    }


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _catalog_entries(catalog: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if catalog.get("format") != "coverage.catalog" or catalog.get("version") != 1:
        raise ValueError("coverage catalog format/version is unsupported")
    entries = catalog.get(key)
    if not isinstance(entries, list):
        raise ValueError(f"coverage catalog {key} must be an array")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "line", "kind", "expr"}:
            raise ValueError(f"invalid coverage catalog {key} entry")
        probe_id = entry["id"]
        if not isinstance(probe_id, str) or not probe_id or probe_id in seen:
            raise ValueError(f"duplicate or invalid coverage probe ID: {probe_id!r}")
        if isinstance(entry["line"], bool) or not isinstance(entry["line"], int) or entry["line"] < 1:
            raise ValueError(f"invalid coverage line for {probe_id}")
        seen.add(probe_id)
        result.append(dict(entry))
    return result


def _empty_hits(entries: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {entry["id"]: {"false": 0, "true": 0} for entry in entries}


def _record_coverage_event(event: ExecutionEvent, branch_hits: dict, condition_hits: dict) -> None:
    target = branch_hits if event.type == "BRANCH" else condition_hits if event.type == "COND" else None
    if target is None or not event.probe_id:
        return
    if event.probe_id not in target:
        raise ValueError(f"trace references unknown coverage probe: {event.probe_id}")
    target[event.probe_id]["true" if _event_truth(event.value) else "false"] += 1


def _event_truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"coverage event value is not boolean: {value!r}")


def _obligation_status(child) -> str:
    if child is None:
        return "uncovered"
    if child.is_leaf:
        cases = getattr(child, "test_cases", set()) or set()
        if cases == {INFEASIBLE_MARKER}:
            return "infeasible"
        if cases == {RANGE_EXCLUDED_MARKER}:
            return "rangeExcluded"
    return "covered"
