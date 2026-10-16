"""Structured result of one Z3 satisfiability check."""

from __future__ import annotations

from dataclasses import dataclass

SOLVER_SAT = "sat"
SOLVER_UNSAT = "unsat"
SOLVER_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SolverResult:
    """One solver check outcome, preserving the sat/unsat/unknown distinction."""

    kind: str
    model: str | None = None
    reason: str | None = None
    timeout: bool = False
