from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute line-level SBFL from CSC and TBFV evidence.")
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--method", required=True, choices=("ochiai", "dstar", "op2"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.core_root.resolve()))
    from csc_engine.sbfl import DEFAULT_SBFL_FILENAME, build_sbfl_report, write_sbfl_report

    result_dir = args.result_dir.resolve()
    records = json.loads((result_dir / "testcases.json").read_text(encoding="utf-8"))
    tbfv = json.loads((result_dir / "refined_tbfv_report.json").read_text(encoding="utf-8"))
    snapshot_path = result_dir / "cct.snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.is_file() else None
    report = build_sbfl_report(records, tbfv, result_dir, args.method, snapshot)
    write_sbfl_report(report, result_dir / DEFAULT_SBFL_FILENAME)
    print(f"SBFL ({args.method}): {report['totals']['candidateLines']} candidate lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
