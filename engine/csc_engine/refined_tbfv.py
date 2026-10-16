"""Offline refined TBFV verification helpers.

The first implementation keeps refined TBFV separate from CSC generation:
CSC produces test cases and trace JSONL files, then this module reads those
artifacts and verifies observed paths against an FSF.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .execution_trace import (
    ExecutionEvent,
    condition_results_from_trace,
    parse_trace_jsonl,
    path_condition_from_condition_results,
    update_expr_with_trace,
)
from .z3_helpers import java_expr_to_z3, parse_result, solver_check_z3_structured
from .solver_result import SOLVER_SAT, SOLVER_UNKNOWN, SOLVER_UNSAT


DEFAULT_FSF_ID = "default"


@dataclass(frozen=True)
class FSFUnit:
    """One functional scenario specification unit."""

    id: str
    T: str
    D: str


@dataclass
class PathContext:
    """The reconstructed path context for one executed test case."""

    test_case_id: str
    trace_path: str
    events: list[ExecutionEvent]
    ct_in: str
    condition_count: int
    trace_status: str = "trusted"
    issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioMatch:
    """Whether a path condition intersects an FSF scenario."""

    fsf_id: str
    matched: bool
    match_formula: str
    solver_status: str


@dataclass
class VerificationResult:
    """The refined TBFV result for one path and one FSF unit."""

    test_case_id: str
    fsf_id: str
    status: str
    ct_in: str
    T: str
    D: str
    wp: str
    verification_formula: str
    counterexample: Optional[dict[str, Any]] = None
    reason: str = ""


def default_fsf() -> list[FSFUnit]:
    """Return the default smoke-test FSF."""

    return [FSFUnit(id=DEFAULT_FSF_ID, T="true", D="true")]


def parse_fsf_text(text: str) -> list[FSFUnit]:
    """Parse a simple FSF text format.

    Supported format:

        [fsf_1]
        T: x > 0
        D: return_value > 0

        [fsf_2]
        T: x <= 0
        D: return_value >= 0

    Section headers are optional. Without a header, ids are assigned as
    ``fsf_1``, ``fsf_2``, ...
    """

    units: list[FSFUnit] = []
    current_id: Optional[str] = None
    current_t: Optional[str] = None
    current_d: Optional[str] = None
    auto_index = 1

    def flush_unit() -> None:
        nonlocal current_id, current_t, current_d, auto_index
        if current_t is None and current_d is None and current_id is None:
            return
        if current_t is None or current_d is None:
            label = current_id or f"fsf_{auto_index}"
            raise ValueError(f"Malformed FSF unit {label}: both T and D are required")

        unit_id = current_id or f"fsf_{auto_index}"
        units.append(FSFUnit(
            id=unit_id,
            T=_normalize_expr(current_t),
            D=_normalize_expr(current_d),
        ))
        current_id = None
        current_t = None
        current_d = None
        auto_index += 1

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            flush_unit()
            current_id = line[1:-1].strip()
            if not current_id:
                raise ValueError("Malformed FSF section: empty id")
            continue

        if line.startswith("T:"):
            if current_t is not None and current_d is not None:
                flush_unit()
            elif current_t is not None:
                raise ValueError("Malformed FSF unit: duplicate T before D")
            current_t = line[2:].strip()
            continue

        if line.startswith("D:"):
            if current_d is not None:
                raise ValueError("Malformed FSF unit: duplicate D")
            current_d = line[2:].strip()
            continue

        raise ValueError(f"Malformed FSF line: {raw_line!r}")

    flush_unit()
    return units or default_fsf()


def find_fsf_file(class_name: str, fsf_dir: str | Path | None = None) -> Optional[Path]:
    """Find a class-named FSF file in ``fsf_dir``.

    The canonical name is ``ClassName_FSF.txt``. The lower-case suffix
    ``ClassName_fsf.txt`` is also accepted because older dataset fixtures use
    that spelling.
    """

    root = Path(fsf_dir) if fsf_dir is not None else Path.cwd()
    if root.is_file():
        return root

    candidate_names = [
        f"{class_name}_FSF.txt",
        f"{class_name}_fsf.txt",
    ]
    if root.exists():
        entries = {entry.name: entry for entry in root.iterdir()}
        for name in candidate_names:
            if name in entries:
                return entries[name]

    candidates = [root / name for name in candidate_names]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_fsf_file(fsf_path: str | Path) -> list[FSFUnit]:
    """Load FSF units from an explicit file path."""

    return parse_fsf_text(Path(fsf_path).read_text(encoding="utf-8"))


def load_fsf(class_name: str, fsf_dir: str | Path | None = None) -> list[FSFUnit]:
    """Load a class-named FSF file or return the default FSF if absent."""

    fsf_path = find_fsf_file(class_name, fsf_dir)
    if fsf_path is None:
        return default_fsf()
    return load_fsf_file(fsf_path)


def load_testcase_records(results_json: str | Path) -> list[dict[str, Any]]:
    """Load csc_tool JSON output records."""

    records = json.loads(Path(results_json).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("CSC results JSON must contain a list of testcase records")
    return records


def derive_ct_in(events: list[ExecutionEvent],
                 input_vars: Optional[list[str]] = None) -> str:
    """Derive the input-state path condition from trace events."""

    results = condition_results_from_trace(events, input_vars or [])
    return path_condition_from_condition_results(results)


def derive_wp(postcondition: str, events: list[ExecutionEvent],
              input_vars: Optional[list[str]] = None) -> str:
    """Derive ``wp(path, postcondition)`` from trace value events.

    ``COND`` events are deliberately ignored here because path membership is
    represented separately by ``Ct_in``.
    """

    normalized = _normalize_expr(postcondition)
    value_events = [event for event in events if event.type in {"ASSIGN", "RETURN"}]
    return update_expr_with_trace(normalized, value_events, input_vars or [])


def build_path_context(record: dict[str, Any],
                       input_vars: Optional[list[str]] = None) -> PathContext:
    """Build a refined TBFV path context from one csc_tool record."""

    trace_path = record.get("trace_path")
    if not trace_path:
        raise ValueError("Testcase record does not contain trace_path")

    events = parse_trace_jsonl(trace_path)
    condition_results = condition_results_from_trace(events, input_vars or [])
    ct_in = path_condition_from_condition_results(condition_results)
    return PathContext(
        test_case_id=_test_case_id_from_record(record),
        trace_path=str(trace_path),
        events=events,
        ct_in=ct_in,
        condition_count=len(condition_results),
    )


def build_match_formula(T: str, ct_in: str) -> str:
    """Build the scenario-path matching formula ``T && Ct_in``."""

    return _conjoin(_normalize_expr(T), _normalize_expr(ct_in))


def build_verification_formula(T: str, ct_in: str, wp_expr: str) -> str:
    """Build the refined TBFV counterexample formula."""

    return _conjoin(
        _normalize_expr(T),
        _normalize_expr(ct_in),
        f"!({_normalize_expr(wp_expr)})",
    )


def match_scenario(T: str, ct_in: str, var_types: dict[str, str]) -> ScenarioMatch:
    """Check whether ``T && Ct_in`` is satisfiable."""

    formula = build_match_formula(T, ct_in)
    try:
        result = solver_check_z3_structured(java_expr_to_z3(formula, var_types), var_types)
    except Exception as exc:
        return ScenarioMatch(
            fsf_id="",
            matched=False,
            match_formula=formula,
            solver_status=f"unsupported: {exc}",
        )
    if result.kind == SOLVER_SAT:
        solver_status = "sat"
    elif result.kind == SOLVER_UNSAT:
        solver_status = "unsat"
    elif result.timeout:
        solver_status = "timeout"
    else:
        solver_status = "unknown"
    return ScenarioMatch(
        fsf_id="",
        matched=result.kind == SOLVER_SAT,
        match_formula=formula,
        solver_status=solver_status,
    )


def verify_scenario(path: PathContext, fsf: FSFUnit,
                    var_types: dict[str, str]) -> VerificationResult:
    """Verify one path against one FSF unit."""

    if path.trace_status != "trusted":
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="skipped",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp="",
            verification_formula="",
            reason=f"trace_status={path.trace_status}",
        )

    match = match_scenario(fsf.T, path.ct_in, var_types)
    match = ScenarioMatch(
        fsf_id=fsf.id,
        matched=match.matched,
        match_formula=match.match_formula,
        solver_status=match.solver_status,
    )
    if match.solver_status.startswith("unsupported"):
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="unsupported",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp="",
            verification_formula=match.match_formula,
            reason=match.solver_status,
        )
    if match.solver_status in ("unknown", "timeout"):
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="unknown",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp="",
            verification_formula=match.match_formula,
            reason="solver_timeout" if match.solver_status == "timeout" else "solver_unknown",
        )
    if not match.matched:
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="skipped",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp="",
            verification_formula=match.match_formula,
            reason="scenario_unmatched",
        )

    try:
        wp_expr = derive_wp(fsf.D, path.events, _input_vars_from_var_types(var_types))
        formula = build_verification_formula(fsf.T, path.ct_in, wp_expr)
        result = solver_check_z3_structured(java_expr_to_z3(formula, var_types), var_types)
    except Exception as exc:
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="unsupported",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp="",
            verification_formula="",
            reason=str(exc),
        )

    if result.kind == SOLVER_UNSAT:
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="pass",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp=wp_expr,
            verification_formula=formula,
        )

    if result.kind == SOLVER_UNKNOWN:
        return VerificationResult(
            test_case_id=path.test_case_id,
            fsf_id=fsf.id,
            status="unknown",
            ct_in=path.ct_in,
            T=fsf.T,
            D=fsf.D,
            wp=wp_expr,
            verification_formula=formula,
            reason="solver_timeout" if result.timeout else "solver_unknown",
        )

    return VerificationResult(
        test_case_id=path.test_case_id,
        fsf_id=fsf.id,
        status="fail",
        ct_in=path.ct_in,
        T=fsf.T,
        D=fsf.D,
        wp=wp_expr,
        verification_formula=formula,
        counterexample=parse_result(result.model),
        reason="SAT(T_i && Ct_in && !wp(path, D_i))",
    )


def verify_record(record: dict[str, Any], fsf_units: list[FSFUnit],
                  var_types: dict[str, str],
                  input_vars: Optional[list[str]] = None) -> list[VerificationResult]:
    """Verify one csc_tool testcase record against all FSF units."""

    if not record.get("trace_path"):
        return [
            VerificationResult(
                test_case_id=_test_case_id_from_record(record),
                fsf_id=fsf.id,
                status="skipped",
                ct_in="",
                T=fsf.T,
                D=fsf.D,
                wp="",
                verification_formula="",
                reason="missing_trace_path",
            )
            for fsf in fsf_units
        ]

    path = build_path_context(record, input_vars or _input_vars_from_var_types(var_types))
    return [verify_scenario(path, fsf, var_types) for fsf in fsf_units]


def verify_testcase_records(records: list[dict[str, Any]], class_name: str,
                            var_types: dict[str, str],
                            fsf_units: list[FSFUnit],
                            fsf_file: str | Path | None = None) -> dict[str, Any]:
    """Verify loaded csc_tool records against loaded FSF units."""

    input_vars = _input_vars_from_var_types(var_types)
    results: list[VerificationResult] = []
    for record in records:
        results.extend(verify_record(record, fsf_units, var_types, input_vars))

    result_dicts = [_result_to_dict(result) for result in results]
    return {
        "class_name": class_name,
        "fsf_file": str(fsf_file) if fsf_file is not None else None,
        "summary": _summarize_results(result_dicts, len(records), len(fsf_units)),
        "results": result_dicts,
    }


def verify_results_file(results_json: str | Path, class_name: str,
                        var_types: dict[str, str],
                        fsf_dir: str | Path | None = None) -> dict[str, Any]:
    """Verify every trace-bearing record in a csc_tool results JSON file."""

    fsf_path = find_fsf_file(class_name, fsf_dir)
    fsf_units = load_fsf_file(fsf_path) if fsf_path is not None else default_fsf()
    records = load_testcase_records(results_json)
    return verify_testcase_records(records, class_name, var_types, fsf_units, fsf_path)


def write_report(report: dict[str, Any], output_path: str | Path) -> None:
    """Write a refined TBFV report as JSON."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def annotate_cct_with_failures(cct_path: str | Path,
                               verification_results: list[VerificationResult] | list[dict[str, Any]],
                               output_dir: str | Path | None = None,
                               testcase_records: Optional[list[dict[str, Any]]] = None,
                               var_types: Optional[dict[str, str]] = None,
                               class_name: Optional[str] = None) -> dict[str, Any]:
    """Annotate a persisted CCT with refined-TBFV failures.

    The CCT structure is preserved; only leaf metadata is updated.
    """

    from .cct import CCT
    from .cct_snapshot import write_cct_snapshot

    cct = CCT.load_from_file(str(cct_path), var_types=var_types)
    if cct is None:
        raise FileNotFoundError(f"CCT not found: {cct_path}")

    marked = 0
    missing: list[str] = []
    for result in verification_results:
        result_dict = _verification_result_as_dict(result)
        if result_dict.get("status") != "fail":
            continue
        test_case_id = str(result_dict.get("test_case_id", ""))
        did_mark = cct.mark_tbfv_failure(
            test_case_id=test_case_id,
            fsf_id=str(result_dict.get("fsf_id", "")),
            counterexample=result_dict.get("counterexample") or {},
            formula=str(result_dict.get("formula", "")),
            reason=str(result_dict.get("reason", "")),
            T=str(result_dict.get("T", "")),
            D=str(result_dict.get("D", "")),
        )
        if did_mark:
            marked += 1
        else:
            missing.append(test_case_id)

    cct.save_to_file(str(cct_path))
    out_dir = Path(output_dir) if output_dir is not None else Path(cct_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    fault_dot = out_dir / (Path(cct_path).stem + "_tbfv_fault.dot")
    cct.save_tbfv_fault_dot(str(fault_dot))
    stats_path = out_dir / "cct_tbfv_stats.json"
    stats = {
        "stage": "refined_tbfv",
        "cct_path": str(cct_path),
        "cct": cct.collect_stats(),
    }
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    resolved_class_name = class_name or Path(cct_path).stem.removesuffix("_cct")
    snapshot_path = out_dir / "cct.snapshot.json"
    snapshot_result = write_cct_snapshot(
        cct,
        resolved_class_name,
        snapshot_path,
    )

    return {
        "marked": marked,
        "missing_test_cases": missing,
        "fault_dot": str(fault_dot),
        "tbfv_stats_json": str(stats_path),
        "tbfv_stats": stats["cct"],
        "cct_snapshot_json": str(snapshot_path),
        "cct_snapshot_warnings": list(snapshot_result.warnings),
    }


def _normalize_expr(expr: str) -> str:
    value = expr.strip()
    return value if value else "true"


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0]


def _conjoin(*exprs: str) -> str:
    parts = [expr.strip() for expr in exprs if expr and expr.strip()]
    if not parts:
        return "true"
    if any(part == "false" for part in parts):
        return "false"
    parts = [part for part in parts if part != "true"]
    if not parts:
        return "true"
    return " && ".join(f"({part})" for part in parts)


def _test_case_id_from_record(record: dict[str, Any]) -> str:
    if record.get("test_case_id"):
        return str(record["test_case_id"])
    status = record.get("status")
    iteration = record.get("iteration")
    if status == "bootstrap" or iteration == 0:
        return "bootstrap"
    branch_idx = record.get("branch_idx")
    if iteration is not None and branch_idx is not None:
        return f"tc_{iteration}_b{branch_idx}"
    if iteration is not None:
        return f"tc_{iteration}"
    return "unknown"


def _input_vars_from_var_types(var_types: dict[str, str]) -> list[str]:
    return [var for var in var_types if var != "return_value"]


def _result_to_dict(result: VerificationResult) -> dict[str, Any]:
    return {
        "test_case_id": result.test_case_id,
        "fsf_id": result.fsf_id,
        "status": result.status,
        "Ct_in": result.ct_in,
        "T": result.T,
        "D": result.D,
        "wp": result.wp,
        "formula": result.verification_formula,
        "counterexample": result.counterexample,
        "reason": result.reason,
    }


def _verification_result_as_dict(result: VerificationResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, VerificationResult):
        return _result_to_dict(result)
    return result


def _summarize_results(result_dicts: list[dict[str, Any]],
                       testcase_count: int,
                       fsf_count: int) -> dict[str, int]:
    summary = {
        "testcases": testcase_count,
        "fsf_units": fsf_count,
        "passed": 0,
        "failed": 0,
        "unknown": 0,
        "skipped": 0,
        "unsupported": 0,
    }
    for result in result_dicts:
        status = result["status"]
        if status == "pass":
            summary["passed"] += 1
        elif status == "fail":
            summary["failed"] += 1
        elif status == "unknown":
            summary["unknown"] += 1
        elif status == "skipped":
            summary["skipped"] += 1
        elif status == "unsupported":
            summary["unsupported"] += 1
    counted = (summary["passed"] + summary["failed"] + summary["unknown"]
               + summary["skipped"] + summary["unsupported"])
    if counted != len(result_dicts):
        raise ValueError(
            f"verification result statuses do not conserve: "
            f"{counted} counted vs {len(result_dicts)} results"
        )
    return summary


def _fsf_file_label(class_name: str, fsf_dir: str | Path | None) -> Optional[str]:
    root = Path(fsf_dir) if fsf_dir is not None else Path.cwd()
    path = root / f"{class_name}_FSF.txt"
    return str(path) if path.exists() else None
