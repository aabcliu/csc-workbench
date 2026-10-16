#!/usr/bin/env python3
"""
CSC Test Case Generator — all-in-one CLI tool.

Usage:
    python3 csc_tool.py <path/to/MyClass.java> [options]

Options:
    --mode MODE         original | expanded (default: expanded)
    --range-bound N     Legacy symmetric range for expanded mode (default: 200)
    --input-intervals J Per-variable interval JSON for expanded mode
    --bootstrap VAL     Bootstrap input value — "var1=5,var2=10" (default: first int param=5)
    --max-iter N        Max CSC iterations (default: 30)
    --strategy STR      sequential | batch (default: sequential)
    --workers N         Worker count for batch strategy (default: 4)
    --no-auto-instrument  Skip auto-instrumentation (Java file is already instrumented)
    --session NAME      Session ID for CCT persistence (default: auto-generated from classname)
    --output FILE       Write generated test cases as JSON to FILE
                        (default: csc_tmp/{session}/{ClassName}/testcases.json)
    --render-cct        Render CCT images during this run
    --cct-formats LIST  Render formats for --render-cct (default: svg,pdf)
    --keep-artifacts    Don't clean up temp files
    -h, --help          Show this help

Examples:
    # Auto-instrument + full CSC loop
    python3 csc_tool.py ../examples/AddLoop.java

    # Already-instrumented file + original CSC mode
    python3 csc_tool.py my_instrumented.java --no-auto-instrument --mode original

    # With bootstrap x=42, max 50 iterations
    python3 csc_tool.py MyClass.java --bootstrap "x=42" --max-iter 50

    # Batch/parallel frontier execution with 4 workers
    python3 csc_tool.py MyClass.java --strategy batch --workers 4
"""

import sys
import os
import re
import json
import subprocess
import shutil
import tempfile
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from csc_engine import (
    CCT, CCT_NOT_INITIALIZED, RANGE_EXCLUDED_MARKER,
    parse_execution_path,
    parse_result, add_value_constraints, add_input_interval_constraints,
    build_input_intervals_from_range_bound, validate_input_intervals,
    batch_discover, batch_verify_and_merge,
    parse_trace_jsonl, condition_results_from_trace,
    write_cct_snapshot,
)
from csc_engine.java_exec import parse_top_level_md_def, parse_class_name
from csc_engine.csc import _save_cct
from csc_engine.analysis_facts import (
    build_coverage_facts,
    build_obligation_facts,
    write_json_atomic,
)
from csc_engine.solver_telemetry import solver_telemetry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRIDGE_JAR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "java_bridge", "target",
                           "csc-bridge-0.1.0-jar-with-dependencies.jar")
DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dataset")
RUNNABLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "dataset", "runnable")
RUNNABLE_DIR = os.environ.get("CSC_RUNNABLE_ROOT", RUNNABLE_DIR)
CSC_TMP_ROOT = os.environ.get("CSC_TMP_ROOT", "csc_tmp")
DEFAULT_CCT_FORMATS = ("svg", "pdf")
SCRIPT_CCT_FORMATS = ("svg", "pdf", "png")


def parse_cct_formats(raw: str) -> list[str]:
    formats = []
    for item in raw.split(","):
        fmt = item.strip().lower().lstrip(".")
        if fmt:
            formats.append(fmt)
    allowed = {"svg", "pdf", "png", "jpg", "jpeg"}
    unsupported = [fmt for fmt in formats if fmt not in allowed]
    if unsupported:
        raise ValueError(f"Unsupported CCT render format(s): {', '.join(unsupported)}")
    return formats or list(DEFAULT_CCT_FORMATS)


def write_render_cct_script(class_dir: str, classname: str,
                            formats: tuple[str, ...] = SCRIPT_CCT_FORMATS) -> str:
    script_path = os.path.join(class_dir, "render_cct.sh")
    dot_files = [
        f"{classname}_cct.dot",
        f"{classname}_cct_tbfv_fault.dot",
    ]
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'cd "$(dirname "$0")"',
        "",
        'if ! command -v dot >/dev/null 2>&1; then',
        '  echo "Graphviz dot command not found. Please install Graphviz first." >&2',
        "  exit 1",
        "fi",
        "",
        "render_dot() {",
        '  local dot_file="$1"',
        '  [ -f "$dot_file" ] || return 0',
    ]
    for fmt in formats:
        if fmt == "png":
            lines.append('  dot -Tpng -Gdpi=200 "$dot_file" -o "${dot_file%.dot}.png"')
        else:
            lines.append(f'  dot -T{fmt} "$dot_file" -o "${{dot_file%.dot}}.{fmt}"')
    lines.extend([
        "}",
        "",
    ])
    for dot_file in dot_files:
        lines.append(f'render_dot "{dot_file}"')
    lines.extend([
        "",
        'echo "Rendered CCT artifacts in $(pwd)"',
        "",
    ])
    with open(script_path, "w") as f:
        f.write("\n".join(lines))
    os.chmod(script_path, 0o755)
    return script_path


def render_cct_artifacts(cct: CCT, dot_path: str, formats: list[str],
                         label: str = "CCT") -> dict[str, str]:
    rendered = {}
    for fmt in formats:
        output_path = cct.render_dot(dot_path, fmt, label=label)
        if output_path:
            rendered[fmt] = output_path
    return rendered


def testcase_record_stats(records: list[dict]) -> dict:
    """Summarize generated testcase records without traversing the CCT."""
    status_counts = {}
    trace_backed = 0
    executable = 0
    for record in records:
        status = record.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if record.get("trace_path"):
            trace_backed += 1
        if status in {"bootstrap", "sat", "batch_safe", "counterexample"}:
            executable += 1
    return {
        "generated_records": len(records),
        "executable_records": executable,
        "trace_backed_records": trace_backed,
        "status_counts": status_counts,
    }


def write_json(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _java_classpath(thread_dir: str) -> str:
    if os.path.exists(BRIDGE_JAR):
        return os.pathsep.join([thread_dir, BRIDGE_JAR])
    return thread_dir


def _compile_runnable_java(java_file: str):
    cmd = ["javac"]
    if os.path.exists(BRIDGE_JAR):
        cmd.extend(["-cp", BRIDGE_JAR])
    cmd.append(java_file)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _execute_runnable_java(thread_dir: str, classname: str, trace_path: str):
    return subprocess.run(
        ["java", f"-Dcsc.trace.file={trace_path}", "-cp", _java_classpath(thread_dir), classname],
        capture_output=True, text=True, timeout=30)


def _ensure_java_execution_succeeded(exec_result, logger, *, iteration: int,
                                      phase: str, inputs: dict) -> None:
    """Stop before trace parsing and CCT merge when Java execution fails."""
    if exec_result.returncode == 0:
        return

    stderr = (exec_result.stderr or "").strip()
    logger.event(
        "execution_error",
        iteration=iteration,
        phase=phase,
        inputs=inputs,
        java_returncode=exec_result.returncode,
        stderr=stderr,
    )
    detail = stderr[-2000:] if stderr else "no stderr was produced"
    raise RuntimeError(
        f"Java execution failed with exit code {exec_result.returncode}: {detail}")


def _trace_path_for(class_dir: str, run_id: str) -> str:
    return f"{class_dir}/traces/{run_id}/trace.jsonl"


def _condition_results_from_trace_or_stdout(stdout: str, trace_path: str, input_vars: list):
    exec_path = parse_execution_path(stdout)
    if os.path.exists(trace_path) and os.path.getsize(trace_path) > 0:
        events = parse_trace_jsonl(trace_path)
        return exec_path, condition_results_from_trace(events, input_vars), len(events)

    raise RuntimeError(
        f"CSCTrace JSONL was not produced at {trace_path}; "
        "stdout path reconstruction is no longer supported."
    )


class RunLogger:
    """Append structured per-run events as JSON Lines."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def event(self, event_type: str, **payload):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")


def find_bridge_jar():
    """Locate the bridge JAR — built JAR first, then check for any in target/."""
    if os.path.exists(BRIDGE_JAR):
        return BRIDGE_JAR
    # Try glob for any matching jar
    target_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "java_bridge", "target")
    if os.path.isdir(target_dir):
        jars = list(Path(target_dir).glob("*-with-dependencies.jar"))
        if jars:
            return str(jars[0])
    return None


def instrumented_output_path(java_file: str) -> str:
    """Return the persistent output path for an auto-instrumented Java file.

    Dataset sources are mirrored under dataset/instrumented/<dataset_name>/...
    so instrumented files can be inspected and reused across runs.
    """
    abs_java_file = os.path.abspath(java_file)
    abs_dataset_dir = os.path.abspath(DATASET_DIR)
    basename = os.path.basename(java_file).replace(".java", "_instrumented.java")

    if os.path.commonpath([abs_java_file, abs_dataset_dir]) == abs_dataset_dir:
        rel_path = os.path.relpath(abs_java_file, abs_dataset_dir)
        rel_parent = os.path.dirname(rel_path)
        if rel_parent.startswith("instrumented" + os.sep) or rel_parent == "instrumented":
            return os.path.join(os.path.dirname(abs_java_file), basename)
        return os.path.join(abs_dataset_dir, "instrumented", rel_parent, basename)

    return abs_java_file.replace(".java", "_instrumented.java")


def is_persistent_instrumented_file(java_file: str) -> bool:
    abs_java_file = os.path.abspath(java_file)
    abs_instrumented_dir = os.path.join(os.path.abspath(DATASET_DIR), "instrumented")
    return os.path.commonpath([abs_java_file, abs_instrumented_dir]) == abs_instrumented_dir


def auto_instrument(java_file: str, coverage_catalog_file: str | None = None) -> str:
    """Run the java_bridge JAR to instrument a Java file. Returns path to instrumented file."""
    jar = find_bridge_jar()
    if jar is None:
        print("Error: java_bridge JAR not found. Build it first:")
        print("  cd java_bridge && mvn clean package")
        print("Or use --no-auto-instrument if your file is already instrumented.")
        sys.exit(1)

    out_file = instrumented_output_path(java_file)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    command = ["java", "-jar", jar, java_file, out_file]
    if coverage_catalog_file is not None:
        command.append(coverage_catalog_file)
    result = subprocess.run(
        command,
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"Instrumentation failed: {result.stderr}")
        sys.exit(1)
    print(f"  [instrument] {java_file} -> {out_file}")
    return out_file


def compile_java(java_file: str) -> str:
    """Compile a Java file. Returns the class name."""
    classname = parse_class_name(open(java_file).read())
    os.makedirs(RUNNABLE_DIR, exist_ok=True)

    # Copy to runnable dir
    dest = os.path.join(RUNNABLE_DIR, f"{classname}.java")
    shutil.copy(java_file, dest)

    result = _compile_runnable_java(dest)
    if result.returncode != 0:
        print(f"Compilation failed:\n{result.stderr}")
        sys.exit(1)
    print(f"  [compile] {classname}.class")
    return classname


def execute_java(classname: str, inputs: dict) -> str:
    """Run a compiled Java class with given inputs. Returns stdout."""
    # Build argument list from inputs dict (in order)
    args = [str(v) for v in inputs.values()]
    result = subprocess.run(
        ["java", "-cp", RUNNABLE_DIR, classname] + args,
        capture_output=True, text=True, timeout=30)
    if result.returncode != 0 and "Exception" in (result.stderr or ""):
        print(f"  [execute] WARNING: {result.stderr[:200]}")
    return result.stdout or ""


def java_literal_for_type(value, java_type: str) -> str:
    """Render a Python/Z3 value as a Java source literal for the target type."""
    if java_type == "char":
        if isinstance(value, int):
            return f"(char){value}"

        raw = str(value)
        stripped = raw.strip()
        if re.fullmatch(r"\(char\)\s*-?\d+", stripped):
            return stripped
        if re.fullmatch(r"-?\d+", stripped):
            return f"(char){stripped}"
        if len(stripped) >= 3 and stripped[0] == "'" and stripped[-1] == "'":
            return stripped
        if len(stripped) == 1 and 32 <= ord(stripped) <= 126 and stripped not in ("'", "\\"):
            return "'" + stripped + "'"
        if len(stripped) == 1:
            return f"(char){ord(stripped)}"
        raise ValueError(f"Cannot render char literal from value: {value!r}")

    if java_type in ("float", "double"):
        return str(value)
    if java_type in ("boolean", "bool"):
        return str(value).lower()
    return str(value)


def build_main_method(java_code: str, classname: str, inputs: dict) -> str:
    """Generate a main method that calls the target function with specific inputs."""
    var_types = parse_top_level_md_def(java_code)
    # Find the target method name
    func_name = None
    for var, vtype in var_types.items():
        if var != "return_value":
            # Look for the method containing this parameter
            for line in java_code.splitlines():
                m = re.search(rf'(?:public|private|protected)\s+static\s+\w+\s+(\w+)\s*\([^)]*{var}[^)]*\)', line)
                if m:
                    func_name = m.group(1)
                    break
            if func_name:
                break

    if func_name is None:
        # Fallback: find first static non-main method
        for line in java_code.splitlines():
            m = re.search(r'static\s+\w+\s+(\w+)\s*\(', line)
            if m and m.group(1) != 'main':
                func_name = m.group(1)
                break

    if func_name is None:
        func_name = "unknown"

    args_str = ", ".join(inputs.keys())

    main = f'''
    public static void main(String[] args) {{
        java.util.Map<String, String> inputs = new java.util.HashMap<>();
        '''

    for var, val in inputs.items():
        vtype = var_types.get(var, "int")
        literal = java_literal_for_type(val, vtype)
        if vtype == "char":
            main += f'        char {var} = {literal};\n'
        elif vtype == "int":
            main += f'        int {var} = {literal};\n'
        elif vtype in ("float", "double"):
            main += f'        double {var} = {literal};\n'
        elif vtype in ("boolean", "bool"):
            main += f'        boolean {var} = {literal};\n'
        else:
            main += f'        int {var} = {literal};\n'

    main += f'''
        {var_types.get("return_value", "int")} r = {func_name}({args_str});
        System.out.println("RETURN_VALUE: {func_name}() = " + r);
    }}
    '''
    return main


def insert_main_method(java_code: str, main_method: str) -> str:
    """Insert a main method into a Java class before its closing brace."""
    lines = java_code.split('\n')
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


def build_default_inputs(var_types: dict) -> dict:
    """Build a default input dict from var types (int=5, char='a', etc.)."""
    defaults = {}
    for var, vtype in var_types.items():
        if var == "return_value":
            continue
        if vtype in ("int", "char"):
            defaults[var] = 0
        elif vtype in ("float", "double"):
            defaults[var] = 0.0
        elif vtype in ("boolean", "bool"):
            defaults[var] = True
        else:
            defaults[var] = 0
    return defaults


def parse_bootstrap_arg(arg_str: str) -> dict:
    """Parse 'var1=5,var2=10' into a dict."""
    result = {}
    for part in arg_str.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            # Try int, then float, then bool, then string
            try:
                result[k] = int(v)
            except ValueError:
                try:
                    result[k] = float(v)
                except ValueError:
                    if v.lower() in ("true", "false"):
                        result[k] = v.lower() == "true"
                    else:
                        result[k] = v
    return result


def main():
    parser = argparse.ArgumentParser(
        description="CSC Test Case Generator — all-in-one CLI",
        add_help=False)
    parser.add_argument("java_file", help="Path to the Java source file")
    parser.add_argument("--mode", choices=["original", "expanded"],
                        default="expanded", help="CSC mode (default: expanded)")
    parser.add_argument("--range-bound", type=int, default=None,
                        help="Legacy symmetric range for expanded mode (default: 200)")
    parser.add_argument(
        "--input-intervals",
        default=None,
        help='Per-variable interval JSON, e.g. \'{"x":{"min":-10,"max":20}}\'',
    )
    parser.add_argument("--bootstrap", type=str, default=None,
                        help='Bootstrap inputs, e.g. "x=5,y=10"')
    parser.add_argument("--max-iter", type=int, default=30,
                        help="Max CSC iterations (default: 30)")
    parser.add_argument("--strategy", choices=["sequential", "batch"],
                        default="sequential",
                        help="Generation strategy: sequential or batch/parallel (default: sequential)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Worker count for --strategy batch (default: 4)")
    parser.add_argument("--no-auto-instrument", action="store_true",
                        help="Skip instrumentation (file already instrumented)")
    parser.add_argument("--session", type=str, default=None,
                        help="Session ID (default: derived from classname)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write generated test cases as JSON to FILE "
                             "(default: csc_tmp/{session}/{ClassName}/testcases.json)")
    parser.add_argument("--render-cct", action="store_true",
                        help="Render CCT artifacts during this run")
    parser.add_argument("--cct-formats", default=",".join(DEFAULT_CCT_FORMATS),
                        help="Comma-separated formats for --render-cct (default: svg,pdf)")
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="Don't clean up temp files")
    parser.add_argument("-h", "--help", action="store_true",
                        help="Show help message")

    args = parser.parse_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    java_file = args.java_file
    if not os.path.exists(java_file):
        print(f"Error: File not found: {java_file}")
        sys.exit(1)

    use_expanded = args.mode == "expanded"
    range_bound = args.range_bound
    if args.input_intervals is not None and range_bound is not None:
        print("Error: use either --input-intervals or --range-bound, not both")
        sys.exit(1)
    if args.input_intervals is not None and not use_expanded:
        print("Error: --input-intervals requires --mode expanded")
        sys.exit(1)
    if args.input_intervals is None and range_bound is None:
        range_bound = 200
    max_iter = args.max_iter
    strategy = args.strategy
    workers = max(1, args.workers)
    try:
        cct_formats = parse_cct_formats(args.cct_formats)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    session_id = args.session or os.path.splitext(os.path.basename(java_file))[0].lower()
    run_started_at = datetime.now(timezone.utc).isoformat()

    print("=" * 55)
    print("  CSC Engine — Test Case Generator")
    print("=" * 55)
    print(f"  Source:       {java_file}")
    print(f"  Mode:         {'Expanded CSC' if use_expanded else 'Original CSC'}")
    if use_expanded and args.input_intervals is not None:
        print("  Input ranges: explicit per-variable intervals")
    elif use_expanded:
        print(f"  Range bound:  +/- {range_bound}")
    print(f"  Max iter:     {max_iter}")
    print(f"  Strategy:     {strategy}")
    if strategy == "batch":
        print(f"  Workers:      {workers}")
    print(f"  Session:      {session_id}")
    print(f"  CCT render:   {'yes (' + ','.join(cct_formats) + ')' if args.render_cct else 'no'}")
    print()

    original_classname = parse_class_name(open(java_file).read())
    class_dir = os.path.join(CSC_TMP_ROOT, session_id, original_classname)
    os.makedirs(class_dir, exist_ok=True)
    coverage_catalog_path = os.path.join(class_dir, "coverage.catalog.v1.json")

    # -------------------------------------------------------------------
    # Step 1: Instrument (or skip)
    # -------------------------------------------------------------------
    if args.no_auto_instrument:
        instrumented_file = java_file
        adjacent_catalog = f"{java_file}.coverage.json"
        if os.path.exists(adjacent_catalog):
            shutil.copyfile(adjacent_catalog, coverage_catalog_path)
        print("[1/3] Skipping instrumentation (--no-auto-instrument)")
    else:
        print("[1/3] Auto-instrumenting Java source...")
        instrumented_file = auto_instrument(java_file, coverage_catalog_path)

    # Read the instrumented source
    java_code = open(instrumented_file).read()
    classname = parse_class_name(java_code)
    var_types = parse_top_level_md_def(java_code)
    input_vars = [v for v in var_types if v != "return_value"]
    try:
        if args.input_intervals is not None:
            parsed_intervals = json.loads(args.input_intervals)
            input_intervals = validate_input_intervals(parsed_intervals, var_types)
        elif use_expanded:
            input_intervals = build_input_intervals_from_range_bound(
                var_types,
                range_bound,
            )
        else:
            input_intervals = {}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Error: invalid input intervals: {exc}")
        sys.exit(1)
    class_dir = os.path.join(CSC_TMP_ROOT, session_id, classname)
    os.makedirs(class_dir, exist_ok=True)
    output_json = args.output or f"{class_dir}/testcases.json"
    log_path = f"{class_dir}/run_log.jsonl"
    logger = RunLogger(log_path)
    logger.event(
        "run_start",
        run_started_at=run_started_at,
        source=java_file,
        instrumented_file=instrumented_file,
        classname=classname,
        mode="expanded" if use_expanded else "original",
        range_bound=(
            range_bound
            if use_expanded and args.input_intervals is None else None
        ),
        input_intervals=input_intervals,
        max_iter=max_iter,
        strategy=strategy,
        workers=workers if strategy == "batch" else None,
        render_cct=args.render_cct,
        cct_formats=cct_formats,
        session=session_id,
        var_types=var_types,
        resumed=os.path.exists(f"{class_dir}/{classname}_cct.pkl"),
    )

    print(f"  Class:        {classname}")
    print(f"  Params:       {var_types}")
    print(f"  Run log:      {log_path}")
    print()

    # -------------------------------------------------------------------
    # Step 2: Compile
    # -------------------------------------------------------------------
    print("[2/3] Compiling...")
    compile_java(instrumented_file)

    # -------------------------------------------------------------------
    # Step 3: CSC Loop
    # -------------------------------------------------------------------
    print("[3/3] CSC Loop")
    print("-" * 45)

    # Determine bootstrap inputs
    if args.bootstrap:
        bootstrap_inputs = parse_bootstrap_arg(args.bootstrap)
    else:
        bootstrap_inputs = build_default_inputs(var_types)

    generated_test_cases = []  # list of {iteration, inputs, path_constraint, status}
    run_timing_totals = {}
    stop_reason = "iteration_limit"
    solver_telemetry.reset()
    cct_pkl = os.path.join(
        CSC_TMP_ROOT, session_id, classname, f"{classname}_cct.pkl"
    )

    for iteration in range(1, max_iter + 1):
        # Load CCT
        if os.path.exists(cct_pkl):
            cct = CCT.load_from_file(cct_pkl, var_types=var_types)
        else:
            cct = CCT(
                use_bounded_range=use_expanded,
                input_intervals=input_intervals,
            )

        if cct is None:
            cct = CCT(
                use_bounded_range=use_expanded,
                input_intervals=input_intervals,
            )

        logger.event(
            "iteration_start",
            iteration=iteration,
            cct_loaded=os.path.exists(cct_pkl),
            cct_initialized=cct.root is not None,
        )
        cct.print_tree()

        if strategy == "batch":
            if cct.root is None:
                print(f"\n  [Batch Round {iteration}] CCT not initialized — running bootstrap")
                print(f"  Bootstrap inputs: {bootstrap_inputs}")
                logger.event("bootstrap_start", iteration=iteration, inputs=bootstrap_inputs)

                main_md = build_main_method(java_code, classname, bootstrap_inputs)
                runnable_code = insert_main_method(java_code, main_md)

                thread_dir = f"{RUNNABLE_DIR}/{session_id}/bootstrap"
                os.makedirs(thread_dir, exist_ok=True)
                tmp_java = f"{thread_dir}/{classname}.java"
                with open(tmp_java, "w") as f:
                    f.write(runnable_code)

                compile_start = time.perf_counter()
                compile_result = _compile_runnable_java(tmp_java)
                compile_time_ms = _ms_since(compile_start)
                if compile_result.returncode != 0:
                    print(f"  -> Bootstrap compile error: {compile_result.stderr[:500]}")
                    logger.event(
                        "compile_error",
                        iteration=iteration,
                        phase="bootstrap",
                        inputs=bootstrap_inputs,
                        compile_time_ms=compile_time_ms,
                        stderr=compile_result.stderr,
                    )
                    stop_reason = "compile_error"
                    break

                exec_start = time.perf_counter()
                trace_path = _trace_path_for(class_dir, "bootstrap")
                exec_result = _execute_runnable_java(thread_dir, classname, trace_path)
                java_exec_time_ms = _ms_since(exec_start)
                _ensure_java_execution_succeeded(
                    exec_result,
                    logger,
                    iteration=iteration,
                    phase="bootstrap",
                    inputs=bootstrap_inputs,
                )

                path_log_parse_start = time.perf_counter()
                exec_path, condition_results, trace_step_count = _condition_results_from_trace_or_stdout(
                    exec_result.stdout, trace_path, input_vars)
                path_log_parse_time_ms = _ms_since(path_log_parse_start)
                cct_path_start = time.perf_counter()
                cct_path_build_time_ms = _ms_since(cct_path_start)

                print(f"  Execution path: {trace_step_count} steps, {len(condition_results)} conditions")

                merge_start = time.perf_counter()
                cct.add_sequence(condition_results, "bootstrap", test_inputs=bootstrap_inputs)
                cct_merge_time_ms = _ms_since(merge_start)
                _save_cct(cct, classname, session_id)
                tc_record = {
                    "iteration": 0,
                    "inputs": bootstrap_inputs,
                    "path_constraint": "bootstrap",
                    "trace_path": trace_path,
                    "status": "bootstrap",
                    "strategy": "batch",
                    "timings": {
                        "compile_time_ms": compile_time_ms,
                        "java_exec_time_ms": java_exec_time_ms,
                        "path_log_parse_time_ms": path_log_parse_time_ms,
                        "cct_path_build_time_ms": cct_path_build_time_ms,
                        "cct_merge_time_ms": cct_merge_time_ms,
                        "solver_time_ms": 0,
                    },
                }
                generated_test_cases.append(tc_record)
                logger.event(
                    "bootstrap_complete",
                    iteration=iteration,
                    status="bootstrap",
                    inputs=bootstrap_inputs,
                    trace_path=trace_path,
                    execution_path_steps=trace_step_count,
                    csc_conditions=len(condition_results),
                    java_returncode=exec_result.returncode,
                    timings=tc_record["timings"],
                )
                continue

            print(f"\n  [Batch Round {iteration}] Discovering frontier branches")
            discovery = batch_discover(java_code, session_id=session_id, use_expanded=use_expanded)
            branches = discovery.get("branches", [])
            terminal_updates = discovery.get("terminal_updates", [])
            for key, value in discovery.get("timings", {}).items():
                if isinstance(value, (int, float)):
                    run_timing_totals[key] = run_timing_totals.get(key, 0) + value
            logger.event(
                "batch_discover_complete",
                iteration=iteration,
                branch_count=len(branches),
                frontier_count=len(branches) + len(terminal_updates),
                terminal_update_count=len(terminal_updates),
                cct_full=discovery.get("cct_full", False),
                discovery_errors=discovery.get("discovery_errors", []),
                timings=discovery.get("timings", {}),
            )

            discovery_errors = discovery.get("discovery_errors", [])
            if discovery_errors and not branches and not terminal_updates:
                print(f"  -> Frontier discovery failed for {len(discovery_errors)} branches; CCT is not proven full.")
                logger.event(
                    "batch_discover_error",
                    iteration=iteration,
                    error_count=len(discovery_errors),
                    errors=discovery_errors,
                )
                stop_reason = "analysis_error"
                break

            if discovery.get("cct_full", False):
                print("  -> CSC fixed point reached — discovered obligations classified.")
                logger.event("cct_full", iteration=iteration, strategy="batch")
                stop_reason = "fixed_point"
                break

            if not branches and not terminal_updates:
                print("  -> No applicable frontier update; CCT is not proven full.")
                logger.event("batch_no_progress", iteration=iteration, strategy="batch")
                stop_reason = "no_progress"
                break

            for branch in branches:
                branch["test_case_id"] = f"tc_{iteration}_b{branch['idx']}"
                branch["main_method"] = build_main_method(java_code, classname, branch["inputs"])

            print(
                f"  -> Executing {len(branches)} candidates with {workers} workers; "
                f"merging {len(terminal_updates)} terminal updates")
            batch_start = time.perf_counter()
            try:
                batch_results = batch_verify_and_merge(
                    java_code,
                    T="true",
                    D="true",
                    branches=branches,
                    var_types=var_types,
                    session_id=session_id,
                    use_expanded=use_expanded,
                    max_workers=workers,
                    batch_id=iteration,
                    terminal_updates=terminal_updates,
                )
            except RuntimeError as exc:
                print(f"  -> Batch merge error: {exc}")
                logger.event(
                    "batch_merge_error",
                    iteration=iteration,
                    error=str(exc),
                )
                stop_reason = "analysis_error"
                break
            batch_wall_time_ms = _ms_since(batch_start)
            branch_inputs = {branch["idx"]: branch.get("inputs") for branch in branches}
            branch_paths = {branch["idx"]: branch.get("path_constraint", "") for branch in branches}

            logger.event(
                "batch_verify_complete",
                iteration=iteration,
                branch_count=len(branches),
                result_count=len(batch_results),
                batch_wall_time_ms=batch_wall_time_ms,
                workers=workers,
            )

            for result in sorted(batch_results, key=lambda r: r.get("idx", -1)):
                idx = result.get("idx")
                test_case_id = result.get("test_case_id") or f"tc_{iteration}_b{idx}"
                status = "batch_safe" if result.get("status") == 0 else (
                    "counterexample" if result.get("status") == 2 else "batch_error")
                tc_record = {
                    "test_case_id": test_case_id,
                    "iteration": iteration,
                    "branch_idx": idx,
                    "inputs": branch_inputs.get(idx),
                    "path_constraint": branch_paths.get(idx) or result.get("path_constraint", ""),
                    "status": status,
                    "strategy": "batch",
                    "counterexample": result.get("counterexample", ""),
                    "trace_path": result.get("trace_path"),
                    "timings": result.get("timings", {}),
                }
                generated_test_cases.append(tc_record)
                logger.event(
                    "batch_branch_result",
                    iteration=iteration,
                    branch_idx=idx,
                    status=status,
                    inputs=tc_record["inputs"],
                    path_constraint=tc_record["path_constraint"],
                    counterexample=tc_record["counterexample"],
                    trace_path=tc_record["trace_path"],
                    timings=tc_record["timings"],
                )
            continue

        # Find uncovered branch
        target = cct.check_for_csc(var_types if use_expanded else None)

        if target is None:
            if cct.root is None:
                # Need bootstrap
                print(f"\n  [Iter {iteration}] CCT not initialized — running bootstrap")
                print(f"  Bootstrap inputs: {bootstrap_inputs}")
                logger.event("bootstrap_start", iteration=iteration, inputs=bootstrap_inputs)

                # Build runnable with main method
                main_md = build_main_method(java_code, classname, bootstrap_inputs)
                runnable_code = insert_main_method(java_code, main_md)

                # Write, compile, and execute
                thread_dir = f"{RUNNABLE_DIR}/{session_id}/bootstrap"
                os.makedirs(thread_dir, exist_ok=True)
                tmp_java = f"{thread_dir}/{classname}.java"
                with open(tmp_java, "w") as f:
                    f.write(runnable_code)

                compile_start = time.perf_counter()
                compile_result = _compile_runnable_java(tmp_java)
                compile_time_ms = _ms_since(compile_start)
                if compile_result.returncode != 0:
                    print(f"  -> Bootstrap compile error: {compile_result.stderr[:500]}")
                    logger.event(
                        "compile_error",
                        iteration=iteration,
                        phase="bootstrap",
                        inputs=bootstrap_inputs,
                        compile_time_ms=compile_time_ms,
                        stderr=compile_result.stderr,
                    )
                    stop_reason = "compile_error"
                    break

                exec_start = time.perf_counter()
                trace_path = _trace_path_for(class_dir, "bootstrap")
                exec_result = _execute_runnable_java(thread_dir, classname, trace_path)
                java_exec_time_ms = _ms_since(exec_start)
                _ensure_java_execution_succeeded(
                    exec_result,
                    logger,
                    iteration=iteration,
                    phase="bootstrap",
                    inputs=bootstrap_inputs,
                )

                path_log_parse_start = time.perf_counter()
                exec_path, condition_results, trace_step_count = _condition_results_from_trace_or_stdout(
                    exec_result.stdout, trace_path, input_vars)
                path_log_parse_time_ms = _ms_since(path_log_parse_start)
                cct_path_start = time.perf_counter()
                cct_path_build_time_ms = _ms_since(cct_path_start)

                print(f"  Execution path: {trace_step_count} steps, {len(condition_results)} conditions")

                merge_start = time.perf_counter()
                cct.add_sequence(condition_results, "bootstrap", test_inputs=bootstrap_inputs)
                cct_merge_time_ms = _ms_since(merge_start)
                _save_cct(cct, classname, session_id)
                tc_record = {
                    "iteration": 0,
                    "inputs": bootstrap_inputs,
                    "path_constraint": "bootstrap",
                    "trace_path": trace_path,
                    "status": "bootstrap",
                    "timings": {
                        "compile_time_ms": compile_time_ms,
                        "java_exec_time_ms": java_exec_time_ms,
                        "path_log_parse_time_ms": path_log_parse_time_ms,
                        "cct_path_build_time_ms": cct_path_build_time_ms,
                        "cct_merge_time_ms": cct_merge_time_ms,
                        "solver_time_ms": 0,
                    },
                }
                generated_test_cases.append(tc_record)
                logger.event(
                    "bootstrap_complete",
                    iteration=iteration,
                    status="bootstrap",
                    inputs=bootstrap_inputs,
                    trace_path=trace_path,
                    execution_path_steps=trace_step_count,
                    csc_conditions=len(condition_results),
                    java_returncode=exec_result.returncode,
                    timings=tc_record["timings"],
                )
                continue
            else:
                print(f"\n  [Iter {iteration}] No further executable CSC target found.")
                # check_for_csc may materialize terminal markers while proving
                # that no executable obligation remains.
                pending = [
                    obligation for obligation in build_obligation_facts(cct, "fixed_point")["obligations"]
                    if obligation["status"] == "uncovered"
                    and (obligation.get("resolution") or {}).get("kind") != "ancestor_stopped"
                ]
                _save_cct(cct, classname, session_id)
                stop_reason = "no_progress" if pending else "fixed_point"
                logger.event("cct_full" if not pending else "cct_no_progress",
                             iteration=iteration, unresolved_frontier=len(pending))
                break

        # We have a target — Z3-solve
        path_constraint = cct.construct_path_constraint(target)
        print(f"\n  [Iter {iteration}] Target: {target[-1].condition.condition_string} "
              f"-> {'T' if target[-1].result else 'F'} "
              f"(cnt={target[-1].condition.loop_count})")
        print(f"  Path constraint: {path_constraint}")
        logger.event(
            "target_selected",
            iteration=iteration,
            condition=target[-1].condition.condition_string,
            result=target[-1].result,
            loop_count=target[-1].condition.loop_count,
            path_constraint=path_constraint,
        )

        use_bounded_witness = (
            use_expanded
            and target
            and cct._has_ancestor_with_same_condition(
                target[:-1],
                target[-1].condition.condition_string,
                target[-1].result,
                target[-1].condition.line_number,
            )
        )
        if use_bounded_witness:
            pc_with_constraints = add_input_interval_constraints(
                path_constraint,
                var_types,
                cct.ensure_input_intervals(var_types),
            )
        else:
            pc_with_constraints = add_value_constraints(path_constraint, var_types)
        from csc_engine import java_expr_to_z3, solver_check_z3_structured
        solver_start = time.perf_counter()
        try:
            z3_expr = java_expr_to_z3(pc_with_constraints, var_types)
            solver_result = solver_check_z3_structured(z3_expr, var_types)
        except Exception as exc:
            logger.event(
                "solver_error",
                iteration=iteration,
                path_constraint=path_constraint,
                constrained_path=pc_with_constraints,
                error=str(exc),
            )
            raise RuntimeError(
                f"Constraint solving failed at iteration {iteration}: {exc}") from exc
        solver_time_ms = _ms_since(solver_start)
        logger.event(
            "solver_complete",
            iteration=iteration,
            status=solver_result.kind,
            solver_result=solver_result.model or solver_result.reason,
            solver_time_ms=solver_time_ms,
        )

        if solver_result.kind == "unsat" and use_bounded_witness:
            global_result = cct._check_globally(target, var_types)
            if global_result.kind == "sat":
                cct.apply_terminal_update(
                    [cr.result for cr in target[:-1]], target[-1].result,
                    RANGE_EXCLUDED_MARKER, {
                        "kind": "range_excluded", "boundedResult": "unsat",
                        "globalResult": "sat",
                    })
                terminal_status = "range_excluded"
            elif global_result.kind == "unsat":
                cct.mark_infeasible(target, {
                    "kind": "solver_unsat", "solverResult": "unsat",
                    "scope": "java_values",
                })
                terminal_status = "infeasible"
            else:
                cct.record_branch_resolution(target, {
                    "kind": "solver_unknown" if global_result.kind == "unknown" else "solver_error",
                    "solverResult": global_result.kind,
                })
                _save_cct(cct, classname, session_id)
                logger.event("solver_unknown", iteration=iteration,
                             path_constraint=path_constraint,
                             reason=global_result.reason)
                stop_reason = "no_progress"
                break
        elif solver_result.kind == "unsat":
            # INFEASIBLE
            cct.mark_infeasible(target, {
                "kind": "solver_unsat", "solverResult": "unsat",
                "scope": "java_values",
            })
            terminal_status = "infeasible"
        if solver_result.kind == "unsat":
            _save_cct(cct, classname, session_id)
            print(f"  -> {terminal_status.upper()} (UNSAT in explored scope)")
            tc_record = {
                "iteration": iteration,
                "inputs": None,
                "path_constraint": path_constraint,
                "status": terminal_status,
                "timings": {
                    "compile_time_ms": 0,
                    "java_exec_time_ms": 0,
                    "path_log_parse_time_ms": 0,
                    "cct_path_build_time_ms": 0,
                    "cct_merge_time_ms": 0,
                    "solver_time_ms": solver_time_ms,
                },
            }
            generated_test_cases.append(tc_record)
            logger.event(
                "branch_terminal",
                iteration=iteration,
                path_constraint=path_constraint,
                timings=tc_record["timings"],
            )
            continue

        if solver_result.kind != "sat":
            cct.record_branch_resolution(target, {
                "kind": "solver_unknown" if solver_result.kind == "unknown" else "solver_error",
                "solverResult": solver_result.kind,
            })
            _save_cct(cct, classname, session_id)
            logger.event("solver_unknown", iteration=iteration,
                         path_constraint=path_constraint,
                         reason=solver_result.reason)
            stop_reason = "no_progress"
            break

        # SAT — parse inputs and execute
        new_inputs = parse_result(solver_result.model)
        print(f"  -> SAT: {new_inputs}")
        logger.event("test_inputs_generated", iteration=iteration, inputs=new_inputs)

        # Execute with new inputs
        main_md = build_main_method(java_code, classname, new_inputs)
        runnable_code = insert_main_method(java_code, main_md)

        thread_dir = f"{RUNNABLE_DIR}/{session_id}/t{iteration}"
        os.makedirs(thread_dir, exist_ok=True)
        tmp_java = f"{thread_dir}/{classname}.java"
        with open(tmp_java, "w") as f:
            f.write(runnable_code)

        compile_start = time.perf_counter()
        compile_result = _compile_runnable_java(tmp_java)
        compile_time_ms = _ms_since(compile_start)
        if compile_result.returncode != 0:
            print(f"  -> Compile error: {compile_result.stderr[:200]}")
            logger.event(
                "compile_error",
                iteration=iteration,
                inputs=new_inputs,
                compile_time_ms=compile_time_ms,
                stderr=compile_result.stderr,
            )
            continue

        exec_start = time.perf_counter()
        trace_path = _trace_path_for(class_dir, f"t{iteration}")
        exec_result = _execute_runnable_java(thread_dir, classname, trace_path)
        java_exec_time_ms = _ms_since(exec_start)
        _ensure_java_execution_succeeded(
            exec_result,
            logger,
            iteration=iteration,
            phase="testcase",
            inputs=new_inputs,
        )

        path_log_parse_start = time.perf_counter()
        exec_path, condition_results, trace_step_count = _condition_results_from_trace_or_stdout(
            exec_result.stdout, trace_path, input_vars)
        path_log_parse_time_ms = _ms_since(path_log_parse_start)
        cct_path_start = time.perf_counter()
        cct_path_build_time_ms = _ms_since(cct_path_start)

        print(f"  Execution path: {trace_step_count} steps, {len(condition_results)} conditions")

        # Add to CCT and save
        merge_start = time.perf_counter()
        cct.add_sequence(condition_results, f"tc_{iteration}", test_inputs=new_inputs)
        cct_merge_time_ms = _ms_since(merge_start)
        _save_cct(cct, classname, session_id)

        tc_record = {
            "iteration": iteration,
            "inputs": new_inputs,
            "path_constraint": path_constraint,
            "trace_path": trace_path,
            "status": "sat",
            "timings": {
                "compile_time_ms": compile_time_ms,
                "java_exec_time_ms": java_exec_time_ms,
                "path_log_parse_time_ms": path_log_parse_time_ms,
                "cct_path_build_time_ms": cct_path_build_time_ms,
                "cct_merge_time_ms": cct_merge_time_ms,
                "solver_time_ms": solver_time_ms,
            },
        }
        generated_test_cases.append(tc_record)
        logger.event(
            "testcase_executed",
            iteration=iteration,
            test_case_id=f"tc_{iteration}",
            status="sat",
            inputs=new_inputs,
            path_constraint=path_constraint,
            trace_path=trace_path,
            execution_path_steps=trace_step_count,
            csc_conditions=len(condition_results),
            java_returncode=exec_result.returncode,
            stdout_chars=len(exec_result.stdout or ""),
            stderr_chars=len(exec_result.stderr or ""),
            timings=tc_record["timings"],
        )

    # -------------------------------------------------------------------
    # Done — print summary
    # -------------------------------------------------------------------
    print("\n" + "=" * 55)
    print(f"  Done! {len(generated_test_cases)} test cases generated in {iteration} iterations.")
    print("=" * 55)

    # Final CCT visualization
    cct_pkl = os.path.join(
        CSC_TMP_ROOT, session_id, classname, f"{classname}_cct.pkl"
    )
    dot_path = os.path.join(
        CSC_TMP_ROOT, session_id, classname, f"{classname}_cct.dot"
    )
    render_script = write_render_cct_script(class_dir, classname)
    rendered_cct = {}
    cct_stats = {}
    cct_stats_path = f"{class_dir}/cct_stats.json"
    cct_snapshot_path = f"{class_dir}/cct.snapshot.json"
    cct_snapshot_warnings = ()
    if os.path.exists(cct_pkl):
        cct = CCT.load_from_file(cct_pkl, var_types=var_types)
        if cct:
            cct.save_dot(dot_path)
            cct_stats = {
                "stage": "csc_generation",
                "class_name": classname,
                "session": session_id,
                "cct": cct.collect_stats(),
                "testcases": testcase_record_stats(generated_test_cases),
            }
            write_json(cct_stats_path, cct_stats)
            obligation_facts_path = f"{class_dir}/csc.obligations.v1.json"
            write_json_atomic(
                obligation_facts_path,
                build_obligation_facts(cct, stop_reason),
            )
            snapshot_result = write_cct_snapshot(
                cct,
                classname,
                cct_snapshot_path,
            )
            cct_snapshot_warnings = snapshot_result.warnings
            logger.event(
                "cct_snapshot_written",
                path=cct_snapshot_path,
                warnings=list(cct_snapshot_warnings),
            )
            if args.render_cct:
                rendered_cct = render_cct_artifacts(cct, dot_path, cct_formats)

    solver_telemetry_path = f"{class_dir}/solver.telemetry.v1.json"
    write_json_atomic(solver_telemetry_path, solver_telemetry.snapshot().to_artifact(stage="csc"))
    coverage_facts_path = None
    if os.path.exists(coverage_catalog_path):
        with open(coverage_catalog_path, encoding="utf-8") as stream:
            coverage_catalog = json.load(stream)
        trace_paths = [
            record.get("trace_path")
            for record in generated_test_cases
            if record.get("trace_path")
        ]
        coverage_facts_path = f"{class_dir}/coverage.facts.v1.json"
        write_json_atomic(
            coverage_facts_path,
            build_coverage_facts(
                coverage_catalog,
                trace_paths,
                expected_trace_count=sum(
                    record.get("status") in {"bootstrap", "sat", "batch_safe", "counterexample"}
                    for record in generated_test_cases
                ),
            ),
        )

    # Print test cases summary
    print("\nGenerated Test Cases:")
    print("-" * 45)
    for tc in generated_test_cases:
        status = tc["status"].upper()
        inputs = tc["inputs"] or "N/A"
        if tc["status"] == "sat":
            inputs_str = ", ".join(f"{k}={v}" for k, v in (tc["inputs"] or {}).items())
            print(f"  tc_{tc['iteration']:02d}  [{status}]  {inputs_str}")
        elif tc["status"].startswith("batch_") or tc["status"] == "counterexample":
            inputs_str = ", ".join(f"{k}={v}" for k, v in (tc["inputs"] or {}).items())
            branch_idx = tc.get("branch_idx", "N/A")
            print(f"  round_{tc['iteration']:02d}/b{branch_idx}  [{status}]  {inputs_str}")
        else:
            print(f"  [{status}]  (bootstrap)" if tc["status"] == "bootstrap" else f"  [{status}]")

    print("\nTiming Summary (ms):")
    print("-" * 45)
    if generated_test_cases:
        totals = {}
        wall_time_keys = {
            "parallel_exec_wall_time_ms",
            "merge_verify_wall_time_ms",
            "batch_total_wall_time_ms",
        }
        non_additive_keys = {"max_workers"}
        wall_times_by_round = {}
        for tc in generated_test_cases:
            for key, value in tc.get("timings", {}).items():
                if key in non_additive_keys:
                    continue
                if key in wall_time_keys:
                    round_key = (key, tc.get("iteration"))
                    wall_times_by_round[round_key] = max(wall_times_by_round.get(round_key, 0), value)
                    continue
                if isinstance(value, dict):
                    continue
                totals[key] = totals.get(key, 0) + value
        for (key, _iteration), value in wall_times_by_round.items():
            totals[key] = totals.get(key, 0) + value
        for key, value in run_timing_totals.items():
            totals[key] = totals.get(key, 0) + value
        for key in [
            "solver_time_ms",
            "frontier_discovery_time_ms",
            "compile_time_ms",
            "java_exec_time_ms",
            "path_log_parse_time_ms",
            "cct_path_build_time_ms",
            "cct_merge_time_ms",
            "verification_solver_time_ms",
            "parallel_exec_wall_time_ms",
            "merge_verify_wall_time_ms",
            "batch_total_wall_time_ms",
        ]:
            if key in totals:
                print(f"  {key}: {totals.get(key, 0)}")
    else:
        totals = {}
        print("  No new test cases were generated in this run.")
        print("  Use a fresh --session value to collect generation timings from scratch.")

    status_counts = {}
    for tc in generated_test_cases:
        status_counts[tc["status"]] = status_counts.get(tc["status"], 0) + 1
    logger.event(
        "run_summary",
        generated_count=len(generated_test_cases),
        iterations=iteration,
        status_counts=status_counts,
        timings=totals,
        output_json=output_json,
        cct_pickle=cct_pkl,
        cct_dot=dot_path,
        cct_stats_json=cct_stats_path if cct_stats else None,
        cct_stats=cct_stats.get("cct") if cct_stats else None,
        cct_snapshot_json=cct_snapshot_path if cct_stats else None,
        cct_snapshot_warnings=list(cct_snapshot_warnings),
        coverage_facts_json=coverage_facts_path,
        solver_telemetry_json=solver_telemetry_path,
        csc_obligations_json=(
            f"{class_dir}/csc.obligations.v1.json" if cct_stats else None
        ),
        exploration_stop_reason=stop_reason,
        testcase_record_stats=cct_stats.get("testcases") if cct_stats else None,
        render_cct_script=render_script,
        rendered_cct=rendered_cct,
    )

    # Write output JSON. Defaults to the session/class result directory so
    # downstream tools can consume a single CSC artifact directory.
    output_parent = os.path.dirname(output_json)
    if output_parent:
        os.makedirs(output_parent, exist_ok=True)
    with open(output_json, "w") as f:
        json.dump(generated_test_cases, f, indent=2)
    print(f"\nTest cases written to: {output_json}")

    # Cleanup
    if not args.keep_artifacts:
        if not args.no_auto_instrument and instrumented_file != java_file:
            if os.path.exists(instrumented_file) and not is_persistent_instrumented_file(instrumented_file):
                os.remove(instrumented_file)
                print(f"  (cleaned up {instrumented_file})")

    print("\nCCT visualization:")
    print(f"  DOT: {dot_path}")
    if cct_stats:
        summary = cct_stats["cct"]
        print(f"  Stats: {cct_stats_path}")
        print(f"  Snapshot: {cct_snapshot_path}")
        for warning in cct_snapshot_warnings:
            print(f"  Snapshot warning: {warning}")
        print("  Counts: "
              f"nodes={summary['total_nodes']}, "
              f"leaves={summary['leaf_nodes']}, "
              f"covered={summary['covered_leaves']}, "
              f"infeasible={summary['infeasible_leaves']}, "
              f"out_of_range={summary['out_of_range_leaves']}, "
              f"expanded={summary['expanded_nodes']}, "
              f"truncated={summary['truncated_leaves']}")
    print(f"  Render script: {render_script}")
    if rendered_cct:
        for fmt, path in rendered_cct.items():
            print(f"  {fmt.upper()}: {path}")
    else:
        print(f"  (render later with: {render_script})")
    print(f"\nRun log:")
    print(f"  JSONL: {log_path}")


if __name__ == "__main__":
    main()
