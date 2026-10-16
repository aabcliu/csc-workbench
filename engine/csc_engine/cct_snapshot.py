"""Build and validate the stable CCT Snapshot v1 representation."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .cct import (
    CCT,
    INFEASIBLE_MARKER,
    RANGE_EXCLUDED_MARKER,
    Condition,
    ConditionResult,
    Node,
)
from .z3_helpers import validate_input_intervals


_NODE_ID_PATTERN = re.compile(r"^n:root(?:\.[FT])*$")
_EDGE_ID_PATTERN = re.compile(r"^e:root(?:\.[FT])+$")
_LEAF_TYPES = {"concrete", "infeasible", "range_excluded"}


class CCTSnapshotError(ValueError):
    """Raised when a CCT cannot be represented as a valid v1 Snapshot."""


@dataclass(frozen=True)
class CCTSnapshotExportResult:
    snapshot: dict[str, Any]
    warnings: tuple[str, ...]


def _require_plain_int(value: Any, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CCTSnapshotError(f"{field} must be an integer")
    if value < minimum:
        raise CCTSnapshotError(f"{field} must be at least {minimum}")
    return value


def _require_string(value: Any, field: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise CCTSnapshotError(f"{field} must be a string")
    if not allow_empty and not value:
        raise CCTSnapshotError(f"{field} must not be empty")
    return value


def _serialize_condition(condition: Condition, field: str) -> dict[str, Any]:
    if not isinstance(condition, Condition):
        raise CCTSnapshotError(f"{field} must be a Condition")
    return {
        "line": _require_plain_int(condition.line_number, f"{field}.line"),
        "expression": _require_string(
            condition.condition_string,
            f"{field}.expression",
        ),
        "inputConstraint": _require_string(
            condition.input_constraint,
            f"{field}.inputConstraint",
        ),
        "occurrence": _require_plain_int(
            condition.loop_count,
            f"{field}.occurrence",
        ),
    }


def _serialize_condition_result(
        result: ConditionResult, field: str) -> dict[str, Any]:
    if not isinstance(result, ConditionResult):
        raise CCTSnapshotError(f"{field} must be a ConditionResult")
    if not isinstance(result.result, bool):
        raise CCTSnapshotError(f"{field}.outcome must be a boolean")
    return {
        "condition": _serialize_condition(result.condition, f"{field}.condition"),
        "outcome": result.result,
    }


def _serialize_remainder(
        remainder: Optional[Sequence[ConditionResult]], field: str
        ) -> Optional[list[dict[str, Any]]]:
    if remainder is None:
        return None
    if not isinstance(remainder, (list, tuple)):
        raise CCTSnapshotError(f"{field} must be a condition-result sequence")
    if not remainder:
        return None
    return [
        _serialize_condition_result(result, f"{field}[{index}]")
        for index, result in enumerate(remainder)
    ]


def _serialize_json_scalar(value: Any, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise CCTSnapshotError(f"{field} must be a finite JSON scalar")


def _serialize_json_value(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise CCTSnapshotError(f"{field} object keys must be strings")
        return {
            key: _serialize_json_value(value[key], f"{field}.{key}")
            for key in sorted(keys)
        }
    if isinstance(value, list):
        return [
            _serialize_json_value(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    return _serialize_json_scalar(value, field)


def _serialize_inputs(inputs: Any, field: str) -> dict[str, Any]:
    if not isinstance(inputs, Mapping):
        raise CCTSnapshotError(f"{field} must be an object")
    variables = list(inputs)
    if any(not isinstance(variable, str) or not variable for variable in variables):
        raise CCTSnapshotError(f"{field} variable names must be non-empty strings")
    return {
        variable: _serialize_json_scalar(
            inputs[variable],
            f"{field}.{variable}",
        )
        for variable in sorted(variables)
    }


def _serialize_by_test_case(
        value: Any,
        field: str,
        test_case_ids: set[str],
        item_serializer,
        ) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CCTSnapshotError(f"{field} must be an object")
    keys = list(value)
    if any(not isinstance(key, str) or not key for key in keys):
        raise CCTSnapshotError(f"{field} keys must be non-empty test-case strings")
    unknown = sorted(set(keys) - test_case_ids)
    if unknown:
        raise CCTSnapshotError(
            f"{field} references tests not present in testCaseIds: {unknown}"
        )
    return {
        key: item_serializer(value[key], f"{field}.{key}")
        for key in sorted(keys)
    }


def _serialize_test_remainder(value: Any, field: str) -> list[dict[str, Any]]:
    serialized = _serialize_remainder(value, field)
    if serialized is None:
        raise CCTSnapshotError(f"{field} must not be an empty remainder")
    return serialized


def _serialize_tbfv_failures(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CCTSnapshotError(f"{field} must be a non-empty failure array")
    failures: list[dict[str, Any]] = []
    for index, entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        if not isinstance(entry, Mapping):
            raise CCTSnapshotError(f"{entry_field} must be an object")
        required = {"fsf_id", "counterexample", "formula", "reason"}
        allowed = {"fsf_id", "counterexample", "formula", "reason", "T", "D"}
        if not required.issubset(set(entry)) or not set(entry).issubset(allowed):
            raise CCTSnapshotError(
                f"{entry_field} must contain {sorted(required)}"
            )
        counterexample = entry["counterexample"]
        if not isinstance(counterexample, Mapping):
            raise CCTSnapshotError(f"{entry_field}.counterexample must be an object")
        failure_item = {
            "fsfId": _require_string(
                entry["fsf_id"],
                f"{entry_field}.fsfId",
                allow_empty=False,
            ),
            "counterexample": _serialize_json_value(
                counterexample,
                f"{entry_field}.counterexample",
            ),
            "formula": _require_string(
                entry["formula"],
                f"{entry_field}.formula",
            ),
            "reason": _require_string(
                entry["reason"],
                f"{entry_field}.reason",
            ),
        }
        if "T" in entry and entry["T"] is not None:
            failure_item["T"] = str(entry["T"])
        if "D" in entry and entry["D"] is not None:
            failure_item["D"] = str(entry["D"])
        failures.append(failure_item)
    return failures


def _serialize_leaf(node: Node, node_id: str) -> dict[str, Any]:
    if node.left is not None or node.right is not None:
        raise CCTSnapshotError(f"leaf {node_id} must not have children")
    if not isinstance(node.is_expanded, bool):
        raise CCTSnapshotError(f"leaf {node_id}.expanded must be a boolean")
    if not isinstance(node.test_cases, set) or not node.test_cases:
        raise CCTSnapshotError(f"leaf {node_id} must contain a test or marker")

    test_cases = node.test_cases
    markers = test_cases & {INFEASIBLE_MARKER, RANGE_EXCLUDED_MARKER}
    if markers:
        if len(test_cases) != 1:
            raise CCTSnapshotError(f"leaf {node_id} mixes markers and test IDs")
        leaf_type = (
            "infeasible"
            if INFEASIBLE_MARKER in markers else "range_excluded"
        )
        test_case_ids: list[str] = []
    else:
        if any(not isinstance(test_case, str) or not test_case for test_case in test_cases):
            raise CCTSnapshotError(
                f"leaf {node_id} test IDs must be non-empty strings"
            )
        leaf_type = "concrete"
        test_case_ids = sorted(test_cases)

    test_case_set = set(test_case_ids)
    inputs = _serialize_by_test_case(
        getattr(node, "test_inputs", None),
        f"leaf {node_id}.inputsByTestCase",
        test_case_set,
        _serialize_inputs,
    )
    remainder = _serialize_remainder(
        getattr(node, "truncated_remainder", None),
        f"leaf {node_id}.truncatedRemainder",
    )
    remainders = _serialize_by_test_case(
        getattr(node, "truncated_remainders", None),
        f"leaf {node_id}.truncatedRemaindersByTestCase",
        test_case_set,
        _serialize_test_remainder,
    )
    failures = _serialize_by_test_case(
        getattr(node, "tbfv_failures", None),
        f"leaf {node_id}.tbfvFailuresByTestCase",
        test_case_set,
        _serialize_tbfv_failures,
    )

    if leaf_type != "concrete" and (inputs or remainder or remainders or failures):
        raise CCTSnapshotError(
            f"{leaf_type} leaf {node_id} must not contain per-test evidence"
        )

    return {
        "id": node_id,
        "kind": "leaf",
        "leafType": leaf_type,
        "testCaseIds": test_case_ids,
        "inputsByTestCase": inputs,
        "truncatedRemainder": remainder,
        "truncatedRemaindersByTestCase": remainders,
        "tbfvFailuresByTestCase": failures,
        "expanded": node.is_expanded,
    }


def _validate_class_name(class_name: Any) -> str:
    class_name = _require_string(class_name, "className", allow_empty=False)
    if class_name != class_name.strip():
        raise CCTSnapshotError("className must not have surrounding whitespace")
    if "." in class_name or "/" in class_name or "\\" in class_name:
        raise CCTSnapshotError("className must be a Java simple class name")
    return class_name


def build_cct_snapshot(cct: CCT, class_name: str) -> CCTSnapshotExportResult:
    """Build and validate a deterministic CCT Snapshot v1 object."""
    if not isinstance(cct, CCT):
        raise CCTSnapshotError("cct must be a CCT instance")
    if cct.root is None:
        raise CCTSnapshotError("cannot export an empty CCT")
    if cct.root.is_leaf:
        raise CCTSnapshotError("CCT root must be a condition node")
    if not isinstance(cct._use_bounded_range, bool):
        raise CCTSnapshotError("CCT mode must be a boolean")
    if cct._input_intervals is None:
        raise CCTSnapshotError(
            "cannot export an expanded legacy CCT before input interval migration"
        )

    try:
        input_intervals = validate_input_intervals(cct._input_intervals)
    except (TypeError, ValueError) as exc:
        raise CCTSnapshotError(f"invalid CCT input intervals: {exc}") from exc
    mode = "expanded" if cct._use_bounded_range else "original"
    if mode == "original" and input_intervals:
        raise CCTSnapshotError("original CCT mode cannot contain input intervals")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_objects: dict[int, str] = {}

    def visit(node: Node, path: str) -> None:
        if not isinstance(node, Node):
            raise CCTSnapshotError(f"n:{path} must be a Node")
        object_id = id(node)
        if object_id in seen_objects:
            raise CCTSnapshotError(
                f"node object at n:{path} is already used at n:{seen_objects[object_id]}"
            )
        seen_objects[object_id] = path
        node_id = f"n:{path}"

        if not isinstance(node.is_leaf, bool):
            raise CCTSnapshotError(f"node {node_id}.is_leaf must be a boolean")
        if node.is_leaf:
            nodes.append(_serialize_leaf(node, node_id))
            return
        if not isinstance(node.is_expanded, bool):
            raise CCTSnapshotError(f"condition {node_id}.expanded must be a boolean")
        nodes.append({
            "id": node_id,
            "kind": "condition",
            "condition": _serialize_condition(
                node.condition,
                f"condition {node_id}",
            ),
            "expanded": node.is_expanded,
        })

        for suffix, outcome, child in (
                ("F", False, node.left),
                ("T", True, node.right)):
            child_path = f"{path}.{suffix}"
            materialized = child is not None
            edges.append({
                "id": f"e:{child_path}",
                "sourceNodeId": node_id,
                "outcome": outcome,
                "allocation": "materialized" if materialized else "unallocated",
                "targetNodeId": f"n:{child_path}" if materialized else None,
            })
            if materialized:
                visit(child, child_path)

    visit(cct.root, "root")
    snapshot = {
        "format": "cct.snapshot",
        "version": 1,
        "className": _validate_class_name(class_name),
        "config": {
            "mode": mode,
            "loopOccurrenceBound": _require_plain_int(
                cct._loop_occurrence_bound,
                "config.loopOccurrenceBound",
            ),
            "inputIntervals": input_intervals,
        },
        "rootNodeId": "n:root",
        "nodes": sorted(nodes, key=lambda node: node["id"]),
        "edges": sorted(edges, key=lambda edge: edge["id"]),
    }
    warnings = validate_cct_snapshot(snapshot)
    return CCTSnapshotExportResult(snapshot=snapshot, warnings=warnings)


def validate_cct_snapshot(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate v1 cross-record invariants and return stable warnings.

    Local field shapes are specified by the JSON Schema. This validator covers
    graph references, tree structure, path IDs, per-test references, interval
    relationships, and cross-leaf duplicate test IDs.
    """
    if not isinstance(snapshot, Mapping):
        raise CCTSnapshotError("snapshot must be an object")
    try:
        json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CCTSnapshotError(f"snapshot is not valid JSON data: {exc}") from exc

    if snapshot.get("format") != "cct.snapshot":
        raise CCTSnapshotError("unsupported snapshot format")
    if type(snapshot.get("version")) is not int or snapshot["version"] != 1:
        raise CCTSnapshotError("unsupported snapshot version")
    if snapshot.get("rootNodeId") != "n:root":
        raise CCTSnapshotError("rootNodeId must be n:root")
    _validate_class_name(snapshot.get("className"))

    config = snapshot.get("config")
    if not isinstance(config, Mapping):
        raise CCTSnapshotError("config must be an object")
    mode = config.get("mode")
    if mode not in {"original", "expanded"}:
        raise CCTSnapshotError("config.mode must be original or expanded")
    _require_plain_int(
        config.get("loopOccurrenceBound"),
        "config.loopOccurrenceBound",
    )
    try:
        intervals = validate_input_intervals(config.get("inputIntervals"))
    except (TypeError, ValueError) as exc:
        raise CCTSnapshotError(f"invalid config.inputIntervals: {exc}") from exc
    if mode == "original" and intervals:
        raise CCTSnapshotError("original mode requires empty inputIntervals")

    nodes = snapshot.get("nodes")
    edges = snapshot.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise CCTSnapshotError("nodes must be a non-empty array")
    if not isinstance(edges, list):
        raise CCTSnapshotError("edges must be an array")

    node_by_id: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, Mapping):
            raise CCTSnapshotError("every node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not _NODE_ID_PATTERN.fullmatch(node_id):
            raise CCTSnapshotError(f"invalid node ID: {node_id!r}")
        if node_id in node_by_id:
            raise CCTSnapshotError(f"duplicate node ID: {node_id}")
        if node.get("kind") not in {"condition", "leaf"}:
            raise CCTSnapshotError(f"node {node_id} has unknown kind")
        node_by_id[node_id] = node
    if [node["id"] for node in nodes] != sorted(node_by_id):
        raise CCTSnapshotError("nodes must be sorted by ID")

    root = node_by_id.get("n:root")
    if root is None or root.get("kind") != "condition":
        raise CCTSnapshotError("n:root must reference a condition node")

    outgoing: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise CCTSnapshotError("every edge must be an object")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not _EDGE_ID_PATTERN.fullmatch(edge_id):
            raise CCTSnapshotError(f"invalid edge ID: {edge_id!r}")
        if edge_id in edge_ids:
            raise CCTSnapshotError(f"duplicate edge ID: {edge_id}")
        edge_ids.add(edge_id)

        source = edge.get("sourceNodeId")
        if not isinstance(source, str):
            raise CCTSnapshotError(f"edge {edge_id} sourceNodeId must be a string")
        if source not in node_by_id:
            raise CCTSnapshotError(f"edge {edge_id} has missing source {source!r}")
        if node_by_id[source].get("kind") != "condition":
            raise CCTSnapshotError(f"edge {edge_id} starts from a leaf")
        outcome = edge.get("outcome")
        if not isinstance(outcome, bool):
            raise CCTSnapshotError(f"edge {edge_id}.outcome must be a boolean")
        suffix = "T" if outcome else "F"
        expected_path = f"{source[2:]}.{suffix}"
        if edge_id != f"e:{expected_path}":
            raise CCTSnapshotError(f"edge {edge_id} does not match its source/outcome path")

        allocation = edge.get("allocation")
        target = edge.get("targetNodeId")
        if allocation == "materialized":
            if target not in node_by_id:
                raise CCTSnapshotError(f"edge {edge_id} has missing target {target!r}")
            if target != f"n:{expected_path}":
                raise CCTSnapshotError(f"edge {edge_id} target does not match its path")
            incoming[target] += 1
        elif allocation == "unallocated":
            if target is not None:
                raise CCTSnapshotError(
                    f"unallocated edge {edge_id} must have a null target"
                )
        else:
            raise CCTSnapshotError(f"edge {edge_id} has unknown allocation")
        outgoing[source].append(edge)
    if [edge["id"] for edge in edges] != sorted(edge_ids):
        raise CCTSnapshotError("edges must be sorted by ID")

    if incoming.get("n:root", 0) != 0:
        raise CCTSnapshotError("root node must not have an incoming edge")
    for node_id in node_by_id:
        if node_id != "n:root" and incoming.get(node_id, 0) != 1:
            raise CCTSnapshotError(
                f"node {node_id} must have exactly one materialized incoming edge"
            )

    test_locations: dict[str, list[str]] = defaultdict(list)
    warnings: list[str] = []
    for node_id, node in node_by_id.items():
        node_edges = outgoing.get(node_id, [])
        if node.get("kind") == "condition":
            outcomes = [edge.get("outcome") for edge in node_edges]
            if len(node_edges) != 2 or sorted(outcomes) != [False, True]:
                raise CCTSnapshotError(
                    f"condition node {node_id} must have one False and one True edge"
                )
            continue
        if node_edges:
            raise CCTSnapshotError(f"leaf node {node_id} must not have outgoing edges")

        leaf_type = node.get("leafType")
        if leaf_type not in _LEAF_TYPES:
            raise CCTSnapshotError(f"leaf {node_id} has unknown leafType")
        test_case_ids = node.get("testCaseIds")
        if not isinstance(test_case_ids, list) or any(
                not isinstance(test_case, str) or not test_case
                for test_case in test_case_ids):
            raise CCTSnapshotError(f"leaf {node_id}.testCaseIds must be strings")
        if test_case_ids != sorted(set(test_case_ids)):
            raise CCTSnapshotError(
                f"leaf {node_id}.testCaseIds must be unique and sorted"
            )
        if leaf_type == "concrete" and not test_case_ids:
            raise CCTSnapshotError(f"concrete leaf {node_id} must contain a test")
        if leaf_type != "concrete" and test_case_ids:
            raise CCTSnapshotError(f"{leaf_type} leaf {node_id} cannot contain tests")

        test_case_set = set(test_case_ids)
        for field in (
                "inputsByTestCase",
                "truncatedRemaindersByTestCase",
                "tbfvFailuresByTestCase"):
            mapping = node.get(field)
            if not isinstance(mapping, Mapping):
                raise CCTSnapshotError(f"leaf {node_id}.{field} must be an object")
            unknown = sorted(set(mapping) - test_case_set)
            if unknown:
                raise CCTSnapshotError(
                    f"leaf {node_id}.{field} references unknown tests: {unknown}"
                )
            if leaf_type != "concrete" and mapping:
                raise CCTSnapshotError(
                    f"{leaf_type} leaf {node_id} cannot contain {field}"
                )
        missing_inputs = sorted(test_case_set - set(node["inputsByTestCase"]))
        for test_case in missing_inputs:
            warnings.append(
                f"test case {test_case!r} at leaf {node_id} has no recorded inputs"
            )
        default_remainder = node.get("truncatedRemainder")
        per_test_remainders = node["truncatedRemaindersByTestCase"]
        if (default_remainder is not None and per_test_remainders
                and all(
                    remainder != default_remainder
                    for remainder in per_test_remainders.values()
                )):
            warnings.append(
                f"leaf {node_id} default truncated remainder does not match "
                "any per-test remainder"
            )
        if leaf_type != "concrete" and node.get("truncatedRemainder") is not None:
            raise CCTSnapshotError(
                f"{leaf_type} leaf {node_id} cannot contain a truncated remainder"
            )
        for test_case in test_case_ids:
            test_locations[test_case].append(node_id)

    visited: set[str] = set()
    stack = ["n:root"]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            raise CCTSnapshotError(f"cycle or repeated path reaches {node_id}")
        visited.add(node_id)
        for edge in outgoing.get(node_id, []):
            target = edge.get("targetNodeId")
            if target is not None:
                stack.append(target)
    unreachable = sorted(set(node_by_id) - visited)
    if unreachable:
        raise CCTSnapshotError(f"snapshot contains unreachable nodes: {unreachable}")

    warnings.extend(
        f"test case {test_case!r} appears in multiple leaves: {locations}"
        for test_case, locations in sorted(test_locations.items())
        if len(locations) > 1
    )
    return tuple(sorted(warnings))


def write_cct_snapshot(
        cct: CCT, class_name: str, output_path: str | Path
        ) -> CCTSnapshotExportResult:
    """Build, validate, and atomically write a CCT Snapshot v1 file."""
    result = build_cct_snapshot(cct, class_name)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(
                result.snapshot,
                stream,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            stream.write("\n")
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return result
