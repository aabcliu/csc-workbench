#!/usr/bin/env python3
"""Exercise the deployed Java → CSC → TBFV → SBFL workflow using stdlib only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds per analysis stage")
    parser.add_argument("--max-iter", type=int, default=30, help="CSC exploration iteration budget")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    def request(path: str, payload=None, *, form: bool = False):
        headers = {}
        data = None
        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(base + path, data=data, headers=headers), timeout=30
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{path}: HTTP {exc.code}: {exc.read().decode()}") from exc

    health = request("/api/health")
    if health["status"] != "ok" or not health["bridge_built"] or not all(health["tools"].values()):
        raise RuntimeError(f"Deployment is not ready: {health}")
    examples = Path(__file__).resolve().parents[1] / "examples"
    workspace = request("/api/workspaces", {
        "name": "AddLoop deployment smoke check",
        "java_source": (examples / "AddLoop.java").read_text(encoding="utf-8"),
        "fsf_source": (examples / "AddLoop.fsf").read_text(encoding="utf-8"),
    }, form=True)
    stages = []

    def run(stage: str, payload: dict):
        job = request(f"/api/jobs/{stage}", payload)
        job_id = job["id"]
        print(f"Running {stage}: {job_id}", flush=True)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            job = request(f"/api/jobs/{job_id}")
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                if job["status"] != "succeeded":
                    raise RuntimeError(f"{stage} did not succeed: {job}")
                if job["finalization_warnings"]:
                    raise RuntimeError(f"{stage} has publication warnings: {job['finalization_warnings']}")
                break
            time.sleep(1)
        else:
            request(f"/api/jobs/{job_id}/cancel", {})
            raise TimeoutError(f"{stage} exceeded {args.timeout}s")
        view = request(f"/api/jobs/{job_id}/outcome")
        request(f"/api/jobs/{job_id}/metrics")
        snapshot = request(f"/api/jobs/{job_id}/cct-snapshot")
        if not snapshot.get("nodes"):
            raise RuntimeError(f"{stage} produced no CCT nodes")
        outcome = view["outcome"]
        stages.append({"stage": stage, "jobId": job_id, "conclusion": outcome["conclusion"]})
        print(f"{stage}: {outcome['conclusion']['status']}", flush=True)
        return job_id, view

    csc_id, csc = run("csc", {
        "workspace_id": workspace["id"], "mode": "expanded", "range_bound": 200,
        "max_iter": args.max_iter, "strategy": "batch", "workers": 2,
        "bootstrap": "x=100,y=99", "render_cct": True,
    })
    if not any(row["stage"] == "tbfv" and row["available"] for row in csc["nextStages"]):
        raise RuntimeError(f"Generation did not enable verification: {csc}")
    tbfv_id, tbfv = run("tbfv", {
        "workspace_id": workspace["id"], "csc_job_id": csc_id, "render_cct": True,
    })
    if tbfv["outcome"]["conclusion"]["status"] != "fail":
        raise RuntimeError("Verification did not detect AddLoop's intentional fault")
    if not any(row["stage"] == "localization" and row["available"] for row in tbfv["nextStages"]):
        raise RuntimeError(f"Verification did not enable localization: {tbfv}")
    loc_id, loc = run("localization", {
        "workspace_id": workspace["id"], "tbfv_job_id": tbfv_id,
        "scoring_strategy": "ochiai",
    })
    if loc["outcome"]["conclusion"]["status"] != "available":
        raise RuntimeError("Fault localization produced no usable ranking")
    evidence = request(f"/api/jobs/{loc_id}/evidence")
    if not evidence.get("candidates"):
        raise RuntimeError("Fault localization produced no evidence candidates")
    print(json.dumps({"status": "passed", "workspaceId": workspace["id"], "stages": stages}, indent=2))


if __name__ == "__main__":
    main()
