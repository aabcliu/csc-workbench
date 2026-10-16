"""Structured execution trace support for CSCTrace JSONL files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .cct import Condition, ConditionResult


def _replace_variables(current_condition: str, variable: str, new_value: str) -> str:
    pattern = rf'\b{re.escape(variable)}\b'
    return re.sub(pattern, str(new_value), current_condition)


@dataclass(frozen=True)
class ExecutionEvent:
    """One structured event emitted by CSCTrace."""

    type: str
    line: int = 0
    kind: str = ""
    order: int = 0
    expr: str = ""
    value: Any = None
    name: str = ""
    java_type: str = ""
    target: str = ""
    rhs: str = ""
    probe_id: str = ""

    @staticmethod
    def from_dict(data: dict) -> "ExecutionEvent":
        event_type = str(data.get("type", "")).upper()
        return ExecutionEvent(
            type=event_type,
            line=_to_int(data.get("line", 0)),
            kind=str(data.get("kind", "")),
            order=_to_int(data.get("order", 0)),
            expr=str(data.get("expr", "")),
            value=data.get("value"),
            name=str(data.get("name", "")),
            java_type=str(data.get("javaType", data.get("java_type", ""))),
            target=str(data.get("target", "")),
            rhs=str(data.get("rhs", "")),
            probe_id=str(data.get("probeId", data.get("probe_id", ""))),
        )


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_trace_jsonl(path: str | Path) -> List[ExecutionEvent]:
    """Read a CSCTrace JSONL file into structured events."""
    with open(path, "r", encoding="utf-8") as f:
        return parse_trace_jsonl_lines(f)


def parse_trace_jsonl_text(text: str) -> List[ExecutionEvent]:
    """Parse CSCTrace JSONL content from a string."""
    return parse_trace_jsonl_lines(text.splitlines())


def parse_trace_jsonl_lines(lines: Iterable[str]) -> List[ExecutionEvent]:
    """Parse JSONL lines, skipping empty lines.

    Raises ``ValueError`` with line context for malformed JSON or missing event
    type, so trace corruption is visible during testing.
    """
    events: List[ExecutionEvent] = []
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed trace JSON at line {line_no}: {raw_line!r}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Trace line {line_no} is not a JSON object: {raw_line!r}")
        event = ExecutionEvent.from_dict(payload)
        if not event.type:
            raise ValueError(f"Trace line {line_no} is missing event type: {raw_line!r}")
        events.append(event)
    return events


def condition_results_from_trace(events: List[ExecutionEvent],
                                 input_vars: Optional[List[str]] = None) -> List[ConditionResult]:
    """Convert COND events into CCT ``ConditionResult`` objects.

    The input constraint is derived by applying simple backwards substitution
    over prior ASSIGN events. This mirrors the current legacy path conversion
    and gives the new trace parser useful semantics before the full TBFV
    migration is complete.
    """
    results: List[ConditionResult] = []
    condition_count_map: dict[tuple[int, str], int] = {}

    for idx, event in enumerate(events):
        if event.type != "COND":
            continue
        expr = event.expr.strip()
        key = (event.line, expr)
        condition_count_map[key] = condition_count_map.get(key, 0) + 1
        loop_count = condition_count_map[key]
        input_constraint = update_expr_with_trace(expr, events[:idx], input_vars or [])
        condition = Condition(
            line_number=event.line,
            condition_string=expr,
            input_constraint=input_constraint,
            loop_count=loop_count,
        )
        results.append(ConditionResult(condition=condition, result=_truthy(event.value)))

    return results


def path_condition_from_trace(events: List[ExecutionEvent],
                              input_vars: Optional[List[str]] = None) -> str:
    """Build a path condition from structured COND events."""
    return path_condition_from_condition_results(
        condition_results_from_trace(events, input_vars or []))


def path_condition_from_condition_results(results: List[ConditionResult]) -> str:
    """Build a conjunction from condition results using WP input constraints."""
    constraints = []
    for cr in results:
        predicate = cr.condition.input_constraint.strip()
        if predicate.startswith("(") and predicate.endswith(")"):
            predicate = predicate[1:-1]
        if cr.result:
            constraints.append(f"({predicate})")
        else:
            constraints.append(f"!({predicate})")
    return " && ".join(constraints) if constraints else "true"


def update_expr_with_trace(expr: str, prior_events: List[ExecutionEvent],
                           input_vars: Optional[List[str]] = None) -> str:
    """Apply backwards assignment substitution to an expression.

    This is deliberately conservative and source-level: assignment RHS strings
    are parenthesized before substitution, while runtime values are ignored.
    """
    updated = expr
    for event in reversed(prior_events):
        if event.type not in {"ASSIGN", "RETURN"}:
            continue
        target = event.target.strip()
        rhs = event.rhs.strip()
        if not target or not rhs:
            continue
        updated = _replace_variables(updated, target, f"({rhs})")

    for input_var in input_vars or []:
        if f"__{input_var}__" in updated:
            updated = _replace_variables(updated, f"__{input_var}__", input_var)

    return updated.strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)
