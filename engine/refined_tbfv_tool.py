#!/usr/bin/env python3
"""CLI wrapper for offline Refined TBFV verification.

This tool intentionally stays separate from csc_tool.py. The CSC phase
produces test cases, trace JSONL files, and a CCT; this tool consumes those
artifacts and verifies the observed paths against an FSF.

Exit code 0 only means the run itself succeeded; the report may contain
status=unknown conclusions (e.g. solver timeout), so callers must interpret
the report rather than treating exit 0 as a verification pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from csc_engine import (
    CCT,
    annotate_cct_with_failures,
    default_fsf,
    find_fsf_file,
    load_fsf_file,
    load_testcase_records,
    parse_class_name,
    parse_top_level_md_def,
    verify_testcase_records,
    write_report,
)
from csc_engine.analysis_facts import write_json_atomic
from csc_engine.solver_telemetry import combine_telemetry_artifacts, solver_telemetry

DEFAULT_CCT_FORMATS = ("svg", "pdf")
SCRIPT_CCT_FORMATS = ("svg", "pdf", "png")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = resolve_config(args)
        telemetry_path = config["report_path"].parent / "solver.telemetry.v1.json"
        upstream_telemetry = None
        if telemetry_path.is_file():
            upstream_telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
        solver_telemetry.reset()
        report = verify_testcase_records(
            records=config["records"],
            class_name=config["class_name"],
            var_types=config["var_types"],
            fsf_units=config["fsf_units"],
            fsf_file=config["fsf_file"],
        )
        write_report(report, config["report_path"])

        annotation = None
        rendered = {}
        render_script = None
        if not args.no_annotate_cct and config["cct_path"] is not None:
            annotation = annotate_cct_with_failures(
                config["cct_path"],
                report["results"],
                config["fault_output_dir"],
                testcase_records=config["records"],
                var_types=config["var_types"],
                class_name=config["class_name"],
            )
            render_script = write_render_cct_script(
                config["fault_output_dir"],
                config["class_name"],
            )
            if args.render_cct:
                rendered = render_cct_artifacts(
                    annotation["fault_dot"],
                    parse_cct_formats(args.cct_formats),
                    label="TBFV fault",
                )

        current_telemetry = solver_telemetry.snapshot().to_artifact(stage="tbfv")
        combined_telemetry = combine_telemetry_artifacts(
            *([upstream_telemetry] if upstream_telemetry is not None else []),
            current_telemetry,
        )
        write_json_atomic(telemetry_path, combined_telemetry)

        print_summary(report, config, annotation, render_script, rendered)
        return exit_code_for(report, args)
    except Exception as exc:
        print(f"Refined TBFV failed: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify CSC trace artifacts with the offline Refined TBFV flow."
    )
    parser.add_argument(
        "results_json",
        nargs="?",
        help="Path to the CSC testcases JSON. Optional when --csc-result-dir is provided.",
    )
    parser.add_argument(
        "--class",
        "--class-name",
        dest="class_name",
        help="Target Java class name. Inferred from run_log, --java-file, or CSC result dir when possible.",
    )
    parser.add_argument(
        "--java-file",
        help="Java source used only for inferring class name and method variable types.",
    )
    parser.add_argument(
        "--run-log",
        help="run_log.jsonl from the CSC session. Used for classname and var_types inference.",
    )
    parser.add_argument(
        "--csc-result-dir",
        "--class-dir",
        dest="csc_result_dir",
        help="CSC result artifact directory, e.g. csc_tmp/session/ClassName. "
             "--class-dir is kept as a backward-compatible alias.",
    )
    parser.add_argument(
        "--fsf",
        help="Explicit FSF file path. Takes precedence over --fsf-dir.",
    )
    parser.add_argument(
        "--fsf-dir",
        help="Directory containing ClassName_FSF.txt or ClassName_fsf.txt.",
    )
    parser.add_argument(
        "--var-types",
        help='Extra or overriding variable types. Accepts JSON or "x:int,y:int,return_value:int".',
    )
    parser.add_argument(
        "--cct",
        help="CCT pickle to annotate. Defaults to <csc-result-dir>/<ClassName>_cct.pkl when present.",
    )
    parser.add_argument(
        "--output",
        help="Report JSON path. Defaults to <csc-result-dir>/refined_tbfv_report.json.",
    )
    parser.add_argument(
        "--fault-output-dir",
        help="Directory for the TBFV fault DOT view. Defaults to the CCT directory.",
    )
    parser.add_argument(
        "--render-cct",
        action="store_true",
        help="Render CCT artifacts during this run.",
    )
    parser.add_argument(
        "--cct-formats",
        default=",".join(DEFAULT_CCT_FORMATS),
        help="Comma-separated formats for --render-cct (default: svg,pdf).",
    )
    parser.add_argument(
        "--no-annotate-cct",
        action="store_true",
        help="Only write the report; do not mark failed leaves or render a fault CCT view.",
    )
    parser.add_argument(
        "--fail-on-violation",
        action="store_true",
        help="Exit with code 1 if any FSF/path verification fails.",
    )
    parser.add_argument(
        "--fail-on-unsupported",
        action="store_true",
        help="Exit with code 1 if any verification result is unsupported.",
    )
    return parser


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


def write_render_cct_script(output_dir: Path, class_name: str,
                            formats: tuple[str, ...] = SCRIPT_CCT_FORMATS) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / "render_cct.sh"
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
        f'render_dot "{class_name}_cct.dot"',
        f'render_dot "{class_name}_cct_tbfv_fault.dot"',
    ])
    lines.extend([
        "",
        'echo "Rendered CCT artifacts in $(pwd)"',
        "",
    ])
    script_path.write_text("\n".join(lines), encoding="utf-8")
    script_path.chmod(0o755)
    return script_path


def render_cct_artifacts(dot_path: str, formats: list[str],
                         label: str = "CCT") -> dict[str, str]:
    renderer = CCT()
    rendered = {}
    for fmt in formats:
        output_path = renderer.render_dot(dot_path, fmt, label=label)
        if output_path:
            rendered[fmt] = output_path
    return rendered


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    explicit_result_dir = Path(args.csc_result_dir) if args.csc_result_dir else None
    results_json = resolve_results_json(args, explicit_result_dir)
    records = load_testcase_records(results_json)
    csc_result_dir = resolve_csc_result_dir(args, records, results_json)
    run_start = load_run_start(resolve_run_log(args, csc_result_dir))

    class_name = resolve_class_name(args, csc_result_dir, run_start)
    if not class_name:
        raise ValueError("class name is required; pass --class or --java-file")

    var_types = resolve_var_types(args, run_start)
    if not var_types:
        raise ValueError("var_types are required; pass --run-log, --java-file, or --var-types")

    fsf_units, fsf_file = resolve_fsf(args, class_name)
    cct_path = resolve_cct_path(args, csc_result_dir, class_name)
    report_path = resolve_report_path(args, csc_result_dir, results_json)
    fault_output_dir = Path(args.fault_output_dir) if args.fault_output_dir else (
        cct_path.parent if cct_path is not None else report_path.parent
    )

    return {
        "results_json": results_json,
        "records": records,
        "class_name": class_name,
        "var_types": var_types,
        "fsf_units": fsf_units,
        "fsf_file": fsf_file,
        "csc_result_dir": csc_result_dir,
        "cct_path": cct_path,
        "report_path": report_path,
        "fault_output_dir": fault_output_dir,
    }


def resolve_results_json(args: argparse.Namespace, csc_result_dir: Path | None) -> Path:
    if args.results_json:
        return Path(args.results_json)
    if csc_result_dir is not None:
        return csc_result_dir / "testcases.json"
    raise ValueError("results_json is required unless --csc-result-dir is provided")


def resolve_csc_result_dir(args: argparse.Namespace,
                           records: list[dict[str, Any]],
                           results_json: Path) -> Path | None:
    if args.csc_result_dir:
        return Path(args.csc_result_dir)
    if args.run_log:
        return Path(args.run_log).parent
    if args.cct:
        return Path(args.cct).parent
    if results_json.name == "testcases.json":
        return results_json.parent

    for record in records:
        trace_path = record.get("trace_path")
        if trace_path:
            path = Path(trace_path)
            if path.name == "trace.jsonl" and len(path.parents) >= 3:
                return path.parents[2]
    return None


def resolve_run_log(args: argparse.Namespace, csc_result_dir: Path | None) -> Path | None:
    if args.run_log:
        return Path(args.run_log)
    if csc_result_dir is None:
        return None
    candidate = csc_result_dir / "run_log.jsonl"
    return candidate if candidate.exists() else None


def load_run_start(run_log: Path | None) -> dict[str, Any]:
    if run_log is None or not run_log.exists():
        return {}

    latest: dict[str, Any] = {}
    for line in run_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "run_start":
            latest = record
    return latest


def resolve_class_name(args: argparse.Namespace,
                       csc_result_dir: Path | None,
                       run_start: dict[str, Any]) -> str | None:
    if args.class_name:
        return args.class_name
    if run_start.get("classname"):
        return str(run_start["classname"])
    if args.java_file:
        return parse_class_name(Path(args.java_file).read_text(encoding="utf-8"))
    if csc_result_dir is not None:
        return csc_result_dir.name
    return None


def resolve_var_types(args: argparse.Namespace, run_start: dict[str, Any]) -> dict[str, str]:
    var_types: dict[str, str] = {}

    if args.java_file:
        java_code = Path(args.java_file).read_text(encoding="utf-8")
        var_types.update(parse_top_level_md_def(java_code))

    run_log_var_types = run_start.get("var_types")
    if isinstance(run_log_var_types, dict):
        var_types.update({str(k): str(v) for k, v in run_log_var_types.items()})

    if args.var_types:
        var_types.update(parse_var_types_arg(args.var_types))

    return var_types


def parse_var_types_arg(raw: str) -> dict[str, str]:
    value = raw.strip()
    if not value:
        return {}
    if value.startswith("{"):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("--var-types JSON must be an object")
        return {str(k): str(v) for k, v in parsed.items()}

    result: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            name, type_name = item.split(":", 1)
        elif "=" in item:
            name, type_name = item.split("=", 1)
        else:
            raise ValueError(f"Malformed --var-types item: {item!r}")
        result[name.strip()] = type_name.strip()
    return result


def resolve_fsf(args: argparse.Namespace, class_name: str):
    if args.fsf:
        fsf_file = Path(args.fsf)
        return load_fsf_file(fsf_file), fsf_file

    fsf_file = find_fsf_file(class_name, args.fsf_dir)
    if fsf_file is None:
        return default_fsf(), None
    return load_fsf_file(fsf_file), fsf_file


def resolve_cct_path(args: argparse.Namespace,
                     csc_result_dir: Path | None,
                     class_name: str) -> Path | None:
    if args.cct:
        return Path(args.cct)
    if csc_result_dir is None:
        return None
    candidate = csc_result_dir / f"{class_name}_cct.pkl"
    return candidate if candidate.exists() else None


def resolve_report_path(args: argparse.Namespace,
                        csc_result_dir: Path | None,
                        results_json: Path) -> Path:
    if args.output:
        return Path(args.output)
    if csc_result_dir is not None:
        return csc_result_dir / "refined_tbfv_report.json"
    return results_json.with_name("refined_tbfv_report.json")


def print_summary(report: dict[str, Any],
                  config: dict[str, Any],
                  annotation: dict[str, Any] | None,
                  render_script: Path | None,
                  rendered: dict[str, str]) -> None:
    summary = report["summary"]
    print("Refined TBFV complete")
    print(f"  Class:       {config['class_name']}")
    print(f"  CSC result:  {config['csc_result_dir'] or '<inferred from results_json>'}")
    print(f"  FSF:         {config['fsf_file'] or '<default true/true>'}")
    print(f"  Report:      {config['report_path']}")
    print(
        "  Summary:     "
        f"testcases={summary['testcases']}, "
        f"fsf_units={summary['fsf_units']}, "
        f"passed={summary['passed']}, "
        f"failed={summary['failed']}, "
        f"unknown={summary['unknown']}, "
        f"skipped={summary['skipped']}, "
        f"unsupported={summary['unsupported']}"
    )
    if annotation is not None:
        print(f"  CCT marked:  {annotation['marked']}")
        if annotation.get("tbfv_stats_json"):
            stats = annotation.get("tbfv_stats", {})
            print(f"  TBFV stats:  {annotation['tbfv_stats_json']}")
            if stats:
                print("  Counts:      "
                      f"nodes={stats.get('total_nodes', 0)}, "
                      f"leaves={stats.get('leaf_nodes', 0)}, "
                      f"fault_leaves={stats.get('tbfv_fault_leaves', 0)}, "
                      f"failure_cases={stats.get('tbfv_failure_cases', 0)}")
        if annotation.get("cct_snapshot_json"):
            print(f"  Snapshot:     {annotation['cct_snapshot_json']}")
            for warning in annotation.get("cct_snapshot_warnings", []):
                print(f"  Snapshot warning: {warning}")
        print(f"  Fault DOT:    {annotation['fault_dot']}")
        if render_script is not None:
            print(f"  Render script: {render_script}")
        for fmt, path in rendered.items():
            print(f"  {fmt.upper()}:         {path}")


def exit_code_for(report: dict[str, Any], args: argparse.Namespace) -> int:
    summary = report["summary"]
    if args.fail_on_violation and summary["failed"] > 0:
        return 1
    if args.fail_on_unsupported and summary["unsupported"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
