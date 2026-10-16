"""Line-level spectrum-based fault localization for public workbench runs."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .execution_trace import ExecutionEvent, _truthy, parse_trace_jsonl
from .refined_tbfv import _test_case_id_from_record


SBFL_FORMAT = "sbfl.localization"
SBFL_VERSION = 1
DEFAULT_SBFL_FILENAME = "sbfl.localization.v1.json"
SBFL_METHODS = frozenset({"ochiai", "dstar", "op2"})
DSTAR_EXPONENT = 2
DSTAR_ZERO_DENOMINATOR = 1_000_000.0
STATEMENT_EVENT_TYPES = frozenset({"ASSIGN", "RETURN"})


class SBFLContractError(ValueError):
    """Raised when an SBFL input or report violates the public contract."""


def suspiciousness(method: str, a_ef: int, a_ep: int, a_nf: int, total_passed: int) -> float:
    """Return the selected SBFL score from the standard spectrum counts."""
    if method not in SBFL_METHODS:
        raise SBFLContractError(f"Unsupported SBFL method: {method}")
    if any(type(value) is not int or value < 0 for value in (a_ef, a_ep, a_nf, total_passed)):
        raise SBFLContractError("SBFL spectrum counts must be non-negative integers")
    if method == "ochiai":
        denominator = math.sqrt((a_ef + a_nf) * (a_ef + a_ep))
        return a_ef / denominator if denominator else 0.0
    if method == "dstar":
        denominator = a_ep + a_nf
        if denominator == 0:
            return DSTAR_ZERO_DENOMINATOR if a_ef > 0 else 0.0
        return (a_ef ** DSTAR_EXPONENT) / denominator
    return a_ef - (a_ep / (total_passed + 1))


def _resolve_trace_path(record: Mapping[str, Any], result_root: Path) -> Path:
    raw = record.get("trace_path")
    if not isinstance(raw, str) or not raw:
        raise SBFLContractError("testcase has no trace_path")
    normalized = raw.replace("\\", "/")
    marker = "/traces/"
    relative = Path("traces") / normalized.split(marker, 1)[1] if marker in normalized else Path(raw)
    candidate = (result_root / relative).resolve()
    root = result_root.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise SBFLContractError("testcase trace is missing or outside the result directory")
    return candidate


def _classify_tests(report: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, list[str]]]:
    results = report.get("results")
    if not isinstance(results, list):
        raise SBFLContractError("refined TBFV report must contain a results list")
    statuses: dict[str, list[str]] = defaultdict(list)
    for row in results:
        if not isinstance(row, Mapping):
            raise SBFLContractError("refined TBFV results must be objects")
        test_id = row.get("test_case_id")
        status = row.get("status")
        if not isinstance(test_id, str) or not test_id or not isinstance(status, str):
            raise SBFLContractError("refined TBFV result has an invalid test or status")
        statuses[test_id].append(status)
    classifications: dict[str, str] = {}
    excluded: dict[str, list[str]] = {}
    for test_id, values in statuses.items():
        if "fail" in values:
            classifications[test_id] = "failed"
        elif "pass" in values:
            classifications[test_id] = "passed"
        else:
            classifications[test_id] = "excluded"
            excluded[test_id] = sorted(set(values))
    return classifications, excluded


def build_sbfl_report(
    testcase_records: list[dict[str, Any]],
    tbfv_report: Mapping[str, Any],
    result_root: str | Path,
    method: str = "ochiai",
    cct_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic source-line SBFL report and optional CCT projection."""
    if method not in SBFL_METHODS:
        raise SBFLContractError(f"Unsupported SBFL method: {method}")
    classifications, excluded_statuses = _classify_tests(tbfv_report)
    records: dict[str, Mapping[str, Any]] = {}
    for record in testcase_records:
        if not isinstance(record, Mapping):
            raise SBFLContractError("testcase records must be objects")
        test_id = _test_case_id_from_record(dict(record))
        if test_id in records:
            raise SBFLContractError(f"duplicate testcase record: {test_id}")
        records[test_id] = record

    coverage: dict[str, set[int]] = {}
    trace_events: dict[str, list[ExecutionEvent]] = {}
    excluded: list[dict[str, Any]] = []
    root = Path(result_root)
    for test_id in sorted(classifications):
        classification = classifications[test_id]
        if classification == "excluded":
            excluded.append({"testId": test_id, "reason": "no_pass_or_fail_result", "statuses": excluded_statuses[test_id]})
            continue
        record = records.get(test_id)
        if record is None:
            raise SBFLContractError(f"verification references absent testcase: {test_id}")
        trace_path = _resolve_trace_path(record, root)
        events = parse_trace_jsonl(trace_path)
        trace_events[test_id] = events
        coverage[test_id] = {event.line for event in events if event.line > 0}

    failed = sorted(test_id for test_id in coverage if classifications[test_id] == "failed")
    passed = sorted(test_id for test_id in coverage if classifications[test_id] == "passed")
    all_lines = sorted(set().union(*coverage.values()) if coverage else set())
    rows: list[dict[str, Any]] = []
    for line in all_lines:
        failed_covering = [test_id for test_id in failed if line in coverage[test_id]]
        passed_covering = [test_id for test_id in passed if line in coverage[test_id]]
        a_ef, a_ep = len(failed_covering), len(passed_covering)
        a_nf, a_np = len(failed) - a_ef, len(passed) - a_ep
        score = round(suspiciousness(method, a_ef, a_ep, a_nf, len(passed)), 8)
        rows.append({
            "rank": 0, "line": line, "score": score, "formula": method,
            "a_ef": a_ef, "a_ep": a_ep, "a_nf": a_nf, "a_np": a_np,
            "totalExecutedBy": a_ef + a_ep,
            "failureFraction": round(a_ef / len(failed), 8) if failed else 0.0,
            "tieGroupSize": 0,
            "failedTestIds": failed_covering, "passedTestIds": passed_covering,
        })
    rows.sort(key=lambda row: (-row["score"], row["line"]))
    score_counts = Counter(row["score"] for row in rows)
    rank, previous_score = 0, None
    for index, row in enumerate(rows, start=1):
        if previous_score is None or row["score"] != previous_score:
            rank, previous_score = index, row["score"]
        row["rank"] = rank
        row["tieGroupSize"] = score_counts[row["score"]]

    report = {
        "format": SBFL_FORMAT, "version": SBFL_VERSION, "method": method,
        "parameters": {"exponent": DSTAR_EXPONENT} if method == "dstar" else {},
        "totals": {"failedTests": len(failed), "passedTests": len(passed), "excludedTests": len(excluded), "candidateLines": len(rows)},
        "excludedTests": excluded, "ranking": rows,
        "cctOverlay": _build_cct_overlay(rows, cct_snapshot, trace_events),
    }
    return validate_sbfl_report(report)


def _build_cct_overlay(
    rows: list[dict[str, Any]],
    snapshot: Mapping[str, Any] | None,
    trace_events: Mapping[str, list[ExecutionEvent]] | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        return {"nodes": {}, "edges": {}}
    by_line = {row["line"]: row for row in rows}
    nodes: dict[str, Any] = {}
    node_rows: dict[str, dict[str, Any]] = {}
    for node in snapshot.get("nodes", []):
        if not isinstance(node, Mapping) or node.get("kind") != "condition":
            continue
        row = by_line.get((node.get("condition") or {}).get("line"))
        if row:
            node_rows[str(node["id"])] = row
            nodes[str(node["id"])] = _overlay_entry(row)
    edge_blocks = _edge_assignment_lines(snapshot, trace_events or {})
    edges: dict[str, Any] = {}
    for edge in snapshot.get("edges", []):
        if not (isinstance(edge, Mapping) and str(edge.get("sourceNodeId")) in node_rows):
            continue
        edge_id = str(edge["id"])
        top = _top_block_row(edge_blocks.get(edge_id, set()), by_line)
        if top is None:
            edges[edge_id] = {**_overlay_entry(node_rows[str(edge["sourceNodeId"])]), "basis": "condition"}
        else:
            edges[edge_id] = {
                **_overlay_entry(top),
                "basis": "assignment",
                "blockLines": sorted(edge_blocks[edge_id] & by_line.keys()),
            }
    return {"nodes": nodes, "edges": edges}


def _edge_assignment_lines(
    snapshot: Mapping[str, Any],
    trace_events: Mapping[str, list[ExecutionEvent]],
) -> dict[str, set[int]]:
    """Attribute assignment lines to the CCT edge they were executed on.

    Mirrors ``failure_localization._collect_trace_segments_from_events``: replay
    each test's ordered trace events against the snapshot tree — every COND
    event descends one level (T/F by its truth value), and ASSIGN/RETURN events
    between two COND events belong to the edge that was just traversed. A path
    suffix without a matching snapshot edge (e.g. loop occurrences beyond
    ``loopOccurrenceBound``) stops attributing events but does not abort the
    replay, so earlier edges of the same test keep their statements.
    """
    edge_ids = {
        str(edge["id"]) for edge in snapshot.get("edges", [])
        if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)
    }
    blocks: dict[str, set[int]] = defaultdict(set)
    for test_id in sorted(trace_events):
        path = "root"
        current_edge: str | None = None
        for event in trace_events[test_id]:
            if event.type == "COND":
                suffix = "T" if _truthy(event.value) else "F"
                path = f"{path}.{suffix}"
                edge_id = f"e:{path}"
                current_edge = edge_id if edge_id in edge_ids else None
            elif current_edge is not None and event.line > 0 and event.type in STATEMENT_EVENT_TYPES:
                blocks[current_edge].add(event.line)
    return blocks


def _top_block_row(block_lines: set[int], by_line: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    ranked = [by_line[line] for line in sorted(block_lines) if line in by_line]
    if not ranked:
        return None
    return max(ranked, key=lambda row: (row["score"], -row["line"]))


def _overlay_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"line": row["line"], "rank": row["rank"], "score": row["score"], "method": row["formula"], "failedCovering": row["a_ef"], "passedCovering": row["a_ep"]}


def validate_sbfl_report(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != SBFL_FORMAT or document.get("version") != 1:
        raise SBFLContractError("unsupported SBFL report format or version")
    if set(document) != {"format", "version", "method", "parameters", "totals", "excludedTests", "ranking", "cctOverlay"}:
        raise SBFLContractError("SBFL report fields do not match version 1")
    if document.get("method") not in SBFL_METHODS:
        raise SBFLContractError("SBFL report uses an unsupported method")
    method = document["method"]
    expected_parameters = {"exponent": DSTAR_EXPONENT} if method == "dstar" else {}
    if document.get("parameters") != expected_parameters:
        raise SBFLContractError("SBFL report parameters do not match its method")
    ranking, totals = document.get("ranking"), document.get("totals")
    total_keys = {"failedTests", "passedTests", "excludedTests", "candidateLines"}
    if (
        not isinstance(ranking, list)
        or not isinstance(totals, dict)
        or set(totals) != total_keys
        or any(type(totals[key]) is not int or totals[key] < 0 for key in total_keys)
        or totals["candidateLines"] != len(ranking)
    ):
        raise SBFLContractError("SBFL report has inconsistent ranking metadata")
    excluded = document.get("excludedTests")
    if not isinstance(excluded, list) or len(excluded) != totals["excludedTests"]:
        raise SBFLContractError("SBFL excluded-test count is inconsistent")
    for item in excluded:
        if (
            not isinstance(item, dict)
            or set(item) != {"testId", "reason", "statuses"}
            or not isinstance(item["testId"], str)
            or item["reason"] != "no_pass_or_fail_result"
            or not isinstance(item["statuses"], list)
        ):
            raise SBFLContractError("SBFL excluded-test record is invalid")
    previous: tuple[float, int] | None = None
    previous_score: float | None = None
    expected_rank = 0
    seen_lines: set[int] = set()
    score_counts = Counter(row.get("score") for row in ranking if isinstance(row, dict))
    row_keys = {
        "rank", "line", "score", "formula", "a_ef", "a_ep", "a_nf", "a_np",
        "totalExecutedBy", "failureFraction", "tieGroupSize", "failedTestIds", "passedTestIds",
    }
    for row in ranking:
        if not isinstance(row, dict):
            raise SBFLContractError("SBFL candidates must be objects")
        if set(row) != row_keys:
            raise SBFLContractError("SBFL candidate fields do not match version 1")
        score, line = row.get("score"), row.get("line")
        if type(line) is not int or line < 1 or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise SBFLContractError("SBFL candidate has an invalid line or score")
        if line in seen_lines:
            raise SBFLContractError("SBFL ranking contains a duplicate source line")
        seen_lines.add(line)
        counts = [row[key] for key in ("a_ef", "a_ep", "a_nf", "a_np")]
        if any(type(value) is not int or value < 0 for value in counts):
            raise SBFLContractError("SBFL candidate spectrum counts are invalid")
        a_ef, a_ep, a_nf, a_np = counts
        if a_ef + a_nf != totals["failedTests"] or a_ep + a_np != totals["passedTests"]:
            raise SBFLContractError("SBFL candidate spectrum counts do not conserve test totals")
        expected_score = round(suspiciousness(method, a_ef, a_ep, a_nf, totals["passedTests"]), 8)
        if row["formula"] != method or float(score) != expected_score:
            raise SBFLContractError("SBFL candidate score does not match its formula")
        if row["totalExecutedBy"] != a_ef + a_ep:
            raise SBFLContractError("SBFL candidate execution total is inconsistent")
        expected_fraction = round(a_ef / totals["failedTests"], 8) if totals["failedTests"] else 0.0
        if row["failureFraction"] != expected_fraction:
            raise SBFLContractError("SBFL candidate failure fraction is inconsistent")
        if row["tieGroupSize"] != score_counts[score]:
            raise SBFLContractError("SBFL candidate tie group is inconsistent")
        for field, expected_count in (("failedTestIds", a_ef), ("passedTestIds", a_ep)):
            ids = row[field]
            if not isinstance(ids, list) or len(ids) != expected_count or ids != sorted(set(ids)) or any(not isinstance(item, str) or not item for item in ids):
                raise SBFLContractError(f"SBFL candidate {field} is invalid")
        current = (-float(score), line)
        if previous is not None and current < previous:
            raise SBFLContractError("SBFL ranking is not in canonical order")
        previous = current
        if previous_score is None or score != previous_score:
            expected_rank = len(seen_lines)
            previous_score = score
        if row["rank"] != expected_rank:
            raise SBFLContractError("SBFL candidate rank is inconsistent")

    overlay = document.get("cctOverlay")
    if not isinstance(overlay, dict) or set(overlay) != {"nodes", "edges"} or not all(isinstance(overlay[key], dict) for key in overlay):
        raise SBFLContractError("SBFL CCT overlay is invalid")
    rows_by_line = {row["line"]: row for row in ranking}
    overlay_keys = {"line", "rank", "score", "method", "failedCovering", "passedCovering"}
    for group in ("nodes", "edges"):
        for item_id, item in overlay[group].items():
            if not isinstance(item_id, str) or not item_id or not isinstance(item, dict) or not overlay_keys <= set(item):
                raise SBFLContractError("SBFL CCT overlay entry is invalid")
            if group == "edges":
                extra = set(item) - overlay_keys
                if extra - {"basis", "blockLines"}:
                    raise SBFLContractError("SBFL CCT overlay edge entry is invalid")
                if item.get("basis", "condition") not in {"condition", "assignment"}:
                    raise SBFLContractError("SBFL CCT overlay edge basis is invalid")
                block_lines = item.get("blockLines", [])
                if not isinstance(block_lines, list) or any(line not in rows_by_line for line in block_lines):
                    raise SBFLContractError("SBFL CCT overlay edge block lines are invalid")
            elif set(item) != overlay_keys:
                raise SBFLContractError("SBFL CCT overlay entry is invalid")
            row = rows_by_line.get(item["line"])
            if row is None or {key: item[key] for key in overlay_keys} != _overlay_entry(row):
                raise SBFLContractError("SBFL CCT overlay does not match its ranked line")
    return json.loads(json.dumps(document, allow_nan=False))


def write_sbfl_report(report: Mapping[str, Any], output_path: str | Path) -> None:
    validated = validate_sbfl_report(dict(report))
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validated, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_sbfl_report(path: str | Path) -> dict[str, Any]:
    return validate_sbfl_report(json.loads(Path(path).read_text(encoding="utf-8")))
