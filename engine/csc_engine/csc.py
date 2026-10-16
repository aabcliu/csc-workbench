"""
CSC (Condition Sequence Coverage) orchestration.

High-level functions that drive the CSC test case generation loop:
  1. generate_tcs_by_csc() — sequential CSC: find one branch, Z3-solve for inputs
  2. batch_discover() — Phase 1: find ALL uncovered branches at once
  3. batch_verify_and_merge() — Phase 3: parallel execute + CCT merge + verify

These are the main entry points that Python applications should use.
"""

import json
import os
import re
import subprocess
import time
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from .cct import CCT, Condition, ConditionResult, Result, CCT_NOT_INITIALIZED
from .z3_helpers import (
    java_expr_to_z3, solver_check_z3, solver_check_z3_structured, parse_result,
    add_value_constraints, build_input_intervals_from_range_bound,
)
from .execution_trace import (
    parse_trace_jsonl,
    condition_results_from_trace,
    path_condition_from_condition_results,
    update_expr_with_trace,
)
from .java_exec import parse_top_level_md_def, parse_class_name, RUNNABLE_DIR

# ---------------------------------------------------------------------------
# Execution path parsing (low-level, consumed by batch verify)
# ---------------------------------------------------------------------------

def parse_execution_path(execution_output: str) -> List[str]:
    """Extract active stdout compatibility lines from an instrumented run.

    Branch conditions, assignments, and returns are now sourced from CSCTrace
    JSONL. Stdout is kept only for compatibility signals that are still printed
    by the runtime or runnable main methods.
    """
    lines = execution_output.splitlines()
    execution_path = []
    for line in lines:
        if (line.startswith("Function input ") or
            line.startswith("REP") or
            "NP detecting: " in line or
            "PARAM_MAP:" in line or
            "RETURN_VALUE:" in line):
            execution_path.append(line)
    return execution_path


def exist_flag_in_path(execution_path: List[str]) -> bool:
    """Check if the danger REP flag appears in the execution path."""
    for step in execution_path:
        if step.startswith("REP"):
            return True
    return False



def keep_throwing_tcs_until_no_more(current_ct: str, new_d: str,
                                     var_types: dict, var_values: dict,
                                     cur_num: int = 0, max_num: int = 10) -> List[str]:
    """Recursively find additional counterexample test cases by excluding previous ones."""
    if cur_num > max_num:
        return ["TOO_MANY_ERROR==1"]
    tcs = []
    tc = ""
    for var, val in var_values.items():
        if var in var_types:
            tc = f"{tc} && {var} == {val}"
    tc = tc.strip().strip("&&").strip()
    tcs.append(tc)
    T_and_Ct_minus_tc = f"({current_ct}) && !({tc})"
    newT_and_Ct_and_not_D = f"({T_and_Ct_minus_tc}) && !({new_d})"
    newT_and_Ct_and_not_D = add_value_constraints(newT_and_Ct_and_not_D, var_types)
    z3_expr = java_expr_to_z3(newT_and_Ct_and_not_D, var_types)
    solver_result = solver_check_z3(z3_expr, var_types)
    if solver_result == "OK":
        return tcs
    else:
        new_var_values = parse_result(solver_result)
        tmp_tcs = keep_throwing_tcs_until_no_more(
            T_and_Ct_minus_tc, new_d, var_types, new_var_values, cur_num + 1, max_num)
        tcs.extend(tmp_tcs)
    return tcs


# ---------------------------------------------------------------------------
# CSC directory management
# ---------------------------------------------------------------------------

CSC_TMP_BASE = os.environ.get("CSC_TMP_ROOT", "csc_tmp")
BRIDGE_JAR = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..",
    "java_bridge",
    "target",
    "csc-bridge-0.1.0-jar-with-dependencies.jar",
))


def _ms_since(start: float) -> int:
    """Elapsed milliseconds from a perf_counter start."""
    return int((time.perf_counter() - start) * 1000)


def _java_classpath(thread_dir: str) -> str:
    if os.path.exists(BRIDGE_JAR):
        return os.pathsep.join([thread_dir, BRIDGE_JAR])
    return thread_dir


def _trace_path_for(class_name: str, session_id: str, run_id: str) -> str:
    return f"{CSC_TMP_BASE}/{session_id}/{class_name}/traces/{run_id}/trace.jsonl"


def _get_cct_path(class_name: str, session_id: str = "default") -> str:
    """Get the CCT pickle file path for a given class and session."""
    class_dir = f"{CSC_TMP_BASE}/{session_id}/{class_name}"
    os.makedirs(class_dir, exist_ok=True)
    return f"{class_dir}/{class_name}_cct.pkl"


def _load_or_create_cct(class_name: str, session_id: str = "default",
                         use_expanded: bool = False, range_bound: int = 200,
                         var_types: Optional[dict] = None) -> CCT:
    """Load existing CCT from disk or create a new one."""
    var_types = var_types or {}
    cct_path = _get_cct_path(class_name, session_id)
    cct = None
    if os.path.exists(cct_path):
        cct = CCT.load_from_file(cct_path, var_types=var_types)
    if cct is None:
        intervals = (
            build_input_intervals_from_range_bound(var_types, range_bound)
            if use_expanded else {}
        )
        cct = CCT(use_bounded_range=use_expanded, input_intervals=intervals)
    return cct


def _save_cct(cct: CCT, class_name: str, session_id: str = "default"):
    """Save CCT to disk and render DOT/PNG."""
    cct_path = _get_cct_path(class_name, session_id)
    cct.save_to_file(cct_path)
    cct.save_dot(cct_path.replace('.pkl', '.dot'))


# ---------------------------------------------------------------------------
# Sequential CSC: one branch at a time
# ---------------------------------------------------------------------------

def generate_tcs_by_csc(program: str, session_id: str = "default",
                         use_expanded: bool = False) -> Result:
    """Find the next uncovered branch in the CCT and Z3-solve for inputs.

    This is the sequential CSC entry point — call this repeatedly until
    it returns status 5 (CCT_FULL) or 6 (CCT_NOT_INITIALIZED).

    Args:
        program: Java source code string.
        session_id: Session ID for CCT persistence isolation.
        use_expanded: Enable expanded CSC (bounded range gate).

    Returns:
        Result with status:
          0 = SAT (test case found, counter_example contains Z3 model)
          1 = INFEASIBLE (branch UNSAT, marked in CCT)
          5 = CCT_FULL (no further executable CSC target found)
          6 = CCT_NOT_INITIALIZED (need bootstrap execution first)
          7 = UNKNOWN (solver could not decide; branch remains open)
    """
    total_start = time.perf_counter()
    class_name = parse_class_name(program)
    var_types = parse_top_level_md_def(program)

    load_start = time.perf_counter()
    cct = _load_or_create_cct(
        class_name,
        session_id,
        use_expanded,
        var_types=var_types,
    )
    cct_load_time_ms = _ms_since(load_start)
    cct.print_tree()

    discover_start = time.perf_counter()
    condition_results = cct.check_for_csc(var_types if use_expanded else None)
    discover_time_ms = _ms_since(discover_start)

    solver_time_ms = 0

    if condition_results:
        new_path = cct.construct_path_constraint(condition_results)
        print(f"Path constraint: {new_path}")
        new_path = add_value_constraints(new_path, var_types)
        solver_start = time.perf_counter()
        z3_expr = java_expr_to_z3(new_path, var_types)
        solver_result = solver_check_z3_structured(z3_expr, var_types)
        solver_time_ms = _ms_since(solver_start)

        if solver_result.kind == "unsat":
            cct.mark_infeasible(condition_results, {
                "kind": "solver_unsat", "solverResult": "unsat",
                "scope": "java_values",
            })
            r = Result(1, "", "")
        elif solver_result.kind == "sat":
            r = Result(0, solver_result.model, "")
        else:
            cct.record_branch_resolution(condition_results, {
                "kind": "solver_unknown", "solverResult": "unknown",
            })
            r = Result(7, "", "")
    elif cct.root is None:
        r = Result(CCT_NOT_INITIALIZED, "", "")
    else:
        r = Result(5, "", "")

    save_start = time.perf_counter()
    _save_cct(cct, class_name, session_id)
    cct_save_time_ms = _ms_since(save_start)
    r.timings = {
        "total_time_ms": _ms_since(total_start),
        "cct_load_time_ms": cct_load_time_ms,
        "frontier_discovery_time_ms": discover_time_ms,
        "solver_time_ms": solver_time_ms,
        "cct_save_time_ms": cct_save_time_ms,
    }
    return r


# ---------------------------------------------------------------------------
# Batch CSC: Phase 1 — Discover all uncovered branches
# ---------------------------------------------------------------------------

def batch_discover(program: str, session_id: str = "default",
                    use_expanded: bool = False) -> dict:
    """Phase 1: Find ALL uncovered branches in the CCT and Z3-solve for inputs.

    Returns:
        dict with keys:
          status: "ok"
          branches: list of {idx, inputs, debug_path}
          cct_full: bool — True if CCT has no uncovered branches
    """
    total_start = time.perf_counter()
    class_name = parse_class_name(program)
    var_types = parse_top_level_md_def(program)

    load_start = time.perf_counter()
    cct = _load_or_create_cct(
        class_name,
        session_id,
        use_expanded,
        var_types=var_types,
    )
    cct_load_time_ms = _ms_since(load_start)
    cct.print_tree()

    discover_start = time.perf_counter()
    # Batch discovery solves frontier branches inside CCT traversal, so Z3
    # needs full input variable types in both original and expanded modes.
    branches = cct.discover_all_uncovered(var_types)
    discovery_errors = list(getattr(cct, "last_discovery_errors", []))
    terminal_updates = list(getattr(cct, "last_terminal_updates", []))
    discover_time_ms = _ms_since(discover_start)

    save_start = time.perf_counter()
    _save_cct(cct, class_name, session_id)
    cct_save_time_ms = _ms_since(save_start)
    solver_time_ms = sum(b.get("solver_time_ms", 0) for b in branches)

    return {
        "status": "ok",
        "branches": branches,
        "terminal_updates": terminal_updates,
        "cct_full": (len(branches) == 0 and len(terminal_updates) == 0
                     and not discovery_errors and cct.root is not None),
        "discovery_errors": discovery_errors,
        "timings": {
            "total_time_ms": _ms_since(total_start),
            "cct_load_time_ms": cct_load_time_ms,
            "frontier_discovery_time_ms": discover_time_ms,
            "solver_time_ms": solver_time_ms,
            "cct_save_time_ms": cct_save_time_ms,
        },
    }


# ---------------------------------------------------------------------------
# Batch CSC: Phase 3 — Parallel execute + merge + verify
# ---------------------------------------------------------------------------


def _insert_main_method(program: str, main_method: str) -> str:
    """Insert a main method into a Java class before its closing brace.

    Tracks brace depth from the top of the file and inserts before the first
    closing brace at depth 0. This correctly handles nested classes.
    """
    lines = program.split('\n')
    depth = 0
    insert_at = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if '{' in stripped:
            depth += stripped.count('{')
        if '}' in stripped:
            depth -= stripped.count('}')
            if depth == 0 and insert_at is None:
                insert_at = i
        if depth < 0:
            break
    if insert_at is not None:
        main_lines = main_method.split('\n')
        for j, ml in enumerate(main_lines):
            lines.insert(insert_at + j, ml)
    return '\n'.join(lines)


def _compile_and_execute_single(program: str, main_method: str,
                                 session_id: str, thread_id: int,
                                 run_id: Optional[str] = None,
                                 timeout_seconds: int = 30):
    """Compile and execute one instrumented program. Thread-safe.

    Returns (execution_path, test_input_str, stdout_output, classname).
    """
    total_start = time.perf_counter()
    classname = parse_class_name(program)
    thread_dir = f"{RUNNABLE_DIR}/{session_id}/t{thread_id}"
    os.makedirs(thread_dir, exist_ok=True)
    file_path = f"{thread_dir}/{classname}.java"

    runnable = _insert_main_method(program, main_method)
    with open(file_path, "w") as f:
        f.write(runnable)

    compile_start = time.perf_counter()
    compile_cmd = ["javac"]
    if os.path.exists(BRIDGE_JAR):
        compile_cmd.extend(["-cp", BRIDGE_JAR])
    compile_cmd.append(file_path)
    compile_result = subprocess.run(
        compile_cmd, check=False,
        capture_output=True, text=True, timeout=timeout_seconds)
    compile_time_ms = _ms_since(compile_start)
    if compile_result.returncode != 0:
        raise RuntimeError(f"Compilation failed: {compile_result.stderr}")

    java_exec_start = time.perf_counter()
    trace_path = _trace_path_for(classname, session_id, run_id or f"t{thread_id}")
    exec_result = subprocess.run(
        ["java", f"-Dcsc.trace.file={trace_path}", "-cp", _java_classpath(thread_dir), classname],
        capture_output=True, text=True, timeout=timeout_seconds)
    java_exec_time_ms = _ms_since(java_exec_start)

    execution_output = exec_result.stdout or ""
    stderr_output = exec_result.stderr or ""

    if exec_result.returncode != 0:
        detail = stderr_output.strip() or "no stderr was produced"
        raise RuntimeError(
            f"Java execution failed with exit code {exec_result.returncode}: {detail}")

    path_parse_start = time.perf_counter()
    execution_path = parse_execution_path(execution_output)
    condition_results_sequence = None
    trace_events = None
    if os.path.exists(trace_path) and os.path.getsize(trace_path) > 0:
        input_vars = [v for v in parse_top_level_md_def(program).keys() if v != "return_value"]
        trace_events = parse_trace_jsonl(trace_path)
        condition_results_sequence = condition_results_from_trace(trace_events, input_vars)
    path_parse_time_ms = _ms_since(path_parse_start)
    test_inputs = []
    for step in execution_path:
        match = re.search(r"Function input \w+ parameter (\w+) = (.+)", step)
        if match:
            test_inputs.append(f"{match.group(1)}={match.group(2)}")
    test_input_str = ", ".join(test_inputs) if test_inputs else None
    timings = {
        "compile_time_ms": compile_time_ms,
        "java_exec_time_ms": java_exec_time_ms,
        "path_log_parse_time_ms": path_parse_time_ms,
        "compile_execute_total_time_ms": _ms_since(total_start),
    }

    return (
        execution_path,
        test_input_str,
        execution_output,
        classname,
        timings,
        condition_results_sequence,
        trace_events,
        trace_path,
    )


def batch_verify_and_merge(program: str, T: str, D: str,
                            branches: List[dict], var_types: dict,
                            session_id: str = "default",
                            use_expanded: bool = False,
                            max_workers: int = 4,
                            batch_id: Optional[int] = None,
                            terminal_updates: Optional[List[dict]] = None) -> List[dict]:
    """Phase 3: Parallel compile+execute, sequential CCT merge + Z3 verify.

    Args:
        program: Base program with print instrumentation (no main method).
        T: Precondition string (e.g., "true").
        D: Danger condition string (e.g., "divisor != 0").
        branches: List of dicts with keys 'idx' and 'main_method'.
        var_types: Dict of variable name -> type.
        session_id: Session ID for temp directory isolation.
        use_expanded: Enable expanded CSC mode.
        max_workers: Number of parallel threads for compile/execute.
        batch_id: Optional batch round ID used to make trace paths unique.
        terminal_updates: Keyed infeasible/range-excluded updates discovered
            from the same read-only frontier snapshot.

    Returns:
        List of result dicts with: idx, status, path_constraint,
        counterexample, exec_time_ms, verify_time_ms.
    """
    batch_total_start = time.perf_counter()
    class_name = parse_class_name(program)
    input_vars = [v for v in var_types.keys() if v != "return_value"]

    cct = _load_or_create_cct(
        class_name,
        session_id,
        use_expanded,
        var_types=var_types,
    )
    terminal_updates = terminal_updates or []

    results = []
    results_lock = threading.Lock()

    # --- Phase 3a: Parallel compile and execute ---
    exec_data = {}

    parallel_exec_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        test_case_ids = {}
        branch_inputs = {}
        for branch in branches:
            idx = branch["idx"]
            main_method = branch["main_method"]
            test_case_ids[idx] = branch.get("test_case_id") or (
                f"tc_{batch_id}_b{idx}" if batch_id is not None else f"tc_b{idx}"
            )
            branch_inputs[idx] = branch.get("inputs")
            run_id = f"round_{batch_id:02d}/b{idx}" if batch_id is not None else f"t{idx}"
            fut = executor.submit(
                _compile_and_execute_single,
                program, main_method, session_id, idx, run_id
            )
            futures[fut] = idx

        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                (
                    execution_path,
                    test_input_str,
                    exec_output,
                    cn,
                    timings,
                    condition_results_sequence,
                    trace_events,
                    trace_path,
                ) = fut.result()
                with results_lock:
                    exec_data[idx] = (
                        execution_path,
                        test_input_str,
                        exec_output,
                        timings,
                        condition_results_sequence,
                        trace_events,
                        trace_path,
                    )
            except Exception as e:
                print(f"  [BATCH] Error executing branch {idx}: {e}")
                with results_lock:
                    results.append({
                        "idx": idx,
                        "test_case_id": test_case_ids.get(idx),
                        "inputs": branch_inputs.get(idx),
                        "status": -2,
                        "counterexample": str(e),
                        "path_constraint": "",
                        "exec_time_ms": 0, "verify_time_ms": 0,
                        "timings": {
                            "compile_time_ms": 0,
                            "java_exec_time_ms": 0,
                            "path_log_parse_time_ms": 0,
                            "cct_path_build_time_ms": 0,
                            "cct_merge_time_ms": 0,
                            "path_constraint_time_ms": 0,
                            "verification_solver_time_ms": 0,
                        },
                    })
    parallel_exec_wall_time_ms = _ms_since(parallel_exec_start)

    # --- Phase 3b: Sequential merge into CCT + verify ---
    merge_verify_start = time.perf_counter()
    merge_items = []
    for serial, update in enumerate(terminal_updates):
        merge_items.append((update["branch_id"], 0, serial, update))
    branch_keys = {
        branch["idx"]: branch.get("branch_id", f"~{branch['idx']:08d}")
        for branch in branches
    }
    for idx in exec_data:
        merge_items.append((branch_keys[idx], 1, idx, idx))

    for _branch_id, item_kind, _serial, payload in sorted(merge_items):
        if item_kind == 0:
            update_status = cct.apply_terminal_update(
                payload["path"], payload["target_side"], payload["marker"],
                payload.get("resolution"))
            if update_status == "incompatible":
                raise RuntimeError(
                    f"Incompatible terminal update at {payload['branch_id']}")
            continue

        idx = payload
        execution_path, test_input_str, exec_output, timings, condition_results_sequence, trace_events, trace_path = exec_data[idx]
        test_case_id = test_case_ids.get(idx) or (
            f"tc_{batch_id}_b{idx}" if batch_id is not None else f"tc_b{idx}"
        )
        test_inputs = branch_inputs.get(idx)

        # Build CSC path and update CCT
        cct_path_start = time.perf_counter()
        if condition_results_sequence is None:
            condition_results_sequence = []
        cct_path_build_time_ms = _ms_since(cct_path_start)
        cct_merge_time_ms = 0
        if condition_results_sequence:
            merge_start = time.perf_counter()
            cct.add_sequence(condition_results_sequence, test_case_id, test_inputs=test_inputs)
            cct_merge_time_ms = _ms_since(merge_start)

        # Z3 verification
        is_safe_path = not exist_flag_in_path(execution_path)
        path_constraint_start = time.perf_counter()
        if condition_results_sequence is not None:
            current_ct = path_condition_from_condition_results(condition_results_sequence)
        else:
            current_ct = "true"
        path_constraint_time_ms = _ms_since(path_constraint_start)

        verify_time_ms = 0
        solver_result = ""

        if not is_safe_path:
            if trace_events is not None:
                new_d = update_expr_with_trace(D, trace_events, input_vars)
            else:
                new_d = D
            negated_d = f"!({new_d})"
            logic_expr = f"({T}) && ({current_ct}) && ({negated_d})"
            logic_expr = add_value_constraints(logic_expr, var_types)
            z3_expr = java_expr_to_z3(logic_expr, var_types)
            verify_start = time.perf_counter()
            solver_result = solver_check_z3(z3_expr, var_types)
            verify_time_ms = _ms_since(verify_start)
        else:
            new_d = "true"

        if solver_result == "" or solver_result == "OK":
            status = 0
            counterexample = ""
        else:
            result_dict = parse_result(solver_result)
            tcs = keep_throwing_tcs_until_no_more(
                current_ct, new_d, var_types, result_dict, 0)
            tcs_str = ";".join(tcs)
            status = 2
            counterexample = tcs_str

        results.append({
            "idx": idx,
            "test_case_id": test_case_id,
            "inputs": test_inputs,
            "status": status,
            "path_constraint": current_ct,
            "counterexample": counterexample,
            "trace_path": trace_path,
            "exec_time_ms": timings["compile_execute_total_time_ms"],
            "verify_time_ms": verify_time_ms,
            "timings": {
                **timings,
                "cct_path_build_time_ms": cct_path_build_time_ms,
                "cct_merge_time_ms": cct_merge_time_ms,
                "path_constraint_time_ms": path_constraint_time_ms,
                "verification_solver_time_ms": verify_time_ms,
                "parallel_exec_wall_time_ms": parallel_exec_wall_time_ms,
                "max_workers": max_workers,
            },
        })
    merge_verify_wall_time_ms = _ms_since(merge_verify_start)

    _save_cct(cct, class_name, session_id)
    batch_total_wall_time_ms = _ms_since(batch_total_start)
    cumulative_timings = {
        "compile_time_ms": 0,
        "java_exec_time_ms": 0,
        "path_log_parse_time_ms": 0,
        "compile_execute_total_time_ms": 0,
        "cct_path_build_time_ms": 0,
        "cct_merge_time_ms": 0,
        "path_constraint_time_ms": 0,
        "verification_solver_time_ms": 0,
    }
    for result in results:
        for key in cumulative_timings:
            cumulative_timings[key] += result.get("timings", {}).get(key, 0)
    for result in results:
        result.setdefault("timings", {})
        result["timings"].update({
            "parallel_exec_wall_time_ms": parallel_exec_wall_time_ms,
            "merge_verify_wall_time_ms": merge_verify_wall_time_ms,
            "batch_total_wall_time_ms": batch_total_wall_time_ms,
            "max_workers": max_workers,
            "cumulative_batch_timings": cumulative_timings,
        })
    return results
