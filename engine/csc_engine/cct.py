"""
Condition Case Tree (CCT) — the core data structure for CSC.

The CCT systematically tracks all explored program paths as a binary tree.
Each internal node holds a Condition; left = False branch, right = True branch.
Leaves hold test case identifiers or infeasibility markers.

Supports two modes:
  - Original CSC: stops exploring a branch when an ancestor with the same
    condition already explored the opposite side (Condition 1).
  - Expanded CSC: adds a bounded-range gate — even when an ancestor match
    exists, exploration continues if in-range Z3 solutions exist.
"""

import json
import os
import pickle
import subprocess
import time
from typing import List, Set, Optional, Any
from dataclasses import dataclass

from .z3_helpers import (
    java_expr_to_z3, solver_check_z3_structured, parse_result,
    add_value_constraints, add_input_interval_constraints,
    build_input_intervals_from_range_bound, validate_input_intervals,
)
from .solver_result import SolverResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INFEASIBLE_MARKER = "X"
RANGE_EXCLUDED_MARKER = "RANGE_EXCLUDED"
CCT_NOT_INITIALIZED = 6

# ---------------------------------------------------------------------------
# Result class
# ---------------------------------------------------------------------------

class Result:
    """Result from a CSC or Z3 operation, JSON-serializable for bridge compatibility."""

    def __init__(self, status: int, counter_example: str, path_constrain: str,
                 dt: str = "", exec_time_ms: int = 0, verify_time_ms: int = 0,
                 timings: dict = None):
        self.status = status
        self.counter_example = counter_example
        self.path_constrain = path_constrain
        self.dt = dt
        self.exec_time_ms = exec_time_ms
        self.verify_time_ms = verify_time_ms
        self.timings = timings or {}

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, json_string: str) -> 'Result':
        data_dict = json.loads(json_string)
        return cls(data_dict["status"], data_dict["counter_example"],
                   data_dict["path_constrain"], data_dict.get("dt", ""),
                   data_dict.get("exec_time_ms", 0),
                   data_dict.get("verify_time_ms", 0),
                   data_dict.get("timings", {}))

    def __str__(self):
        return (f"Result(status={self.status}, counter_example={self.counter_example}, "
                f"path_constrain={self.path_constrain}, dt={self.dt})")


# ---------------------------------------------------------------------------
# Condition and ConditionResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Condition:
    """A program condition (l, c, k) with its WP-derived input constraint.

    line_number (l): Source line number.
    condition_string (c): Original condition expression (e.g., 'x >= 2').
    input_constraint: The WP-derived constraint on initial inputs.
    loop_count (k): The k-th time this condition is executed in the path.
    """
    line_number: int
    condition_string: str
    input_constraint: str
    loop_count: int = 1

    def __eq__(self, other):
        if not isinstance(other, Condition):
            return NotImplemented
        return (self.line_number == other.line_number and
                self.condition_string == other.condition_string and
                self.loop_count == other.loop_count)

    def __hash__(self):
        return hash((self.line_number, self.condition_string, self.loop_count))


@dataclass(frozen=True)
class ConditionResult:
    """A condition paired with its evaluation result ((l, c, k), T/F)."""
    condition: Condition
    result: bool


def _strip_outer_parens(s: str) -> str:
    """Remove balanced outer parentheses only. Does NOT strip chars individually.

    _strip_outer_parens('(x < 0)') -> 'x < 0'
    _strip_outer_parens('!(x < 0)') -> '!(x < 0)'  (preserved — no outer paren)
    _strip_outer_parens('(!(x >= 521))') -> '!(x >= 521)'  (one balanced pair)
    """
    s = s.strip()
    while s.startswith('(') and s.endswith(')'):
        # Only remove if these are balanced parentheses
        depth = 0
        balanced = True
        for i, ch in enumerate(s):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth == 0 and i < len(s) - 1:
                balanced = False
                break
        if balanced and depth == 0:
            s = s[1:-1]
        else:
            break
    return s


def parse_path_info(path_info_list: List[str]) -> List[ConditionResult]:
    """Convert CSC path strings into ConditionResult objects.

    Format: 'condition_string,result_bool,input_constraint,loop_count'

    Example: '(x >= 2),True,x >= 2 && y == 0,1'
    """
    results = []
    for info_str in path_info_list:
        try:
            parts = [part.strip() for part in info_str.split(',')]
            if len(parts) < 4:
                print(f"Warning: Skipping malformed line: {info_str}")
                continue

            condition_string = _strip_outer_parens(parts[0])
            result_bool_str = parts[1].strip()
            input_constraint = parts[2].strip()
            loop_count_str = parts[3].strip()

            result_bool = result_bool_str.lower() == 'true'
            loop_count = int(loop_count_str)

            cond = Condition(
                line_number=1,
                condition_string=condition_string,
                input_constraint=input_constraint,
                loop_count=loop_count
            )
            cr = ConditionResult(condition=cond, result=result_bool)
            results.append(cr)
        except Exception as e:
            print(f"Error parsing '{info_str}': {e}")
            continue
    return results


# ---------------------------------------------------------------------------
# CCT Node
# ---------------------------------------------------------------------------

class Node:
    """Internal node or leaf node of the Condition Case Tree."""

    def __init__(self, data: Any, is_leaf: bool):
        self.is_leaf: bool = is_leaf
        self.left: Optional['Node'] = None   # F (False) branch
        self.right: Optional['Node'] = None  # T (True) branch

        # Expanded CSC fields
        self.is_expanded: bool = False
        self.truncated_remainder: Optional[List['ConditionResult']] = None
        self.truncated_remainders: Optional[dict] = None
        # Diagnostic for contradictory solver results on an observed truncated
        # path. Historical files may also contain range-stop annotations here;
        # new range stops use the original RANGE_EXCLUDED terminal marker.
        self.truncation_resolution: Optional[dict] = None
        # Provenance is optional so historical pickles without these fields
        # remain readable and conservatively unresolved.
        self.terminal_resolution: Optional[dict] = None
        self.branch_resolutions: dict[bool, dict] = {}

        if is_leaf:
            self.test_cases: Set[str] = {data}
            self.condition: Optional[Condition] = None
            self.test_inputs: Optional[dict] = {}  # test_case_id -> {"var": value, ...}
            self.tbfv_failures: Optional[dict] = {}
        else:
            self.condition: Condition = data
            self.test_cases: Optional[Set[str]] = None
            self.test_inputs: Optional[dict] = None
            self.tbfv_failures: Optional[dict] = None

    def add_test_case(self, test_case: str, inputs: dict = None):
        if self.is_leaf and self.test_cases is not None:
            if INFEASIBLE_MARKER not in self.test_cases and RANGE_EXCLUDED_MARKER not in self.test_cases:
                self.test_cases.add(test_case)
                if inputs is not None and self.test_inputs is not None:
                    self.test_inputs[test_case] = inputs

    def set_test_inputs(self, tc_inputs: dict):
        """Set test case -> inputs mapping. tc_inputs: {test_case_id: {var: value}}"""
        if self.is_leaf and self.test_inputs is not None:
            self.test_inputs.update(tc_inputs)

    def __repr__(self):
        if self.is_leaf:
            if self.test_cases == {INFEASIBLE_MARKER}:
                return "Leaf(Infeasible=X)"
            if self.test_cases == {RANGE_EXCLUDED_MARKER}:
                return "Leaf(Out-of-Range)"
            flags = []
            if self.truncated_remainder is not None and len(self.truncated_remainder) > 0:
                flags.append("TRUNCATED")
            if self.is_expanded:
                flags.append("EXPANDED")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            sorted_cases = sorted(list(self.test_cases))
            # Show inputs if available
            inputs_str = ""
            if self.test_inputs:
                for tc in sorted_cases[:3]:
                    if tc in self.test_inputs:
                        inp = self.test_inputs[tc]
                        inp_parts = [f"{k}={v}" for k, v in inp.items()]
                        inputs_str += f" {tc}({', '.join(inp_parts)})"
            if inputs_str:
                return f"Leaf(Cases={sorted_cases}){flag_str} [{inputs_str.strip()}]"
            return f"Leaf(Cases={sorted_cases}){flag_str}"
        else:
            expanded = " [EXPANDED]" if self.is_expanded else ""
            return (f"Node(Cond='{self.condition.condition_string}' "
                    f"@L{self.condition.line_number} "
                    f"(Cnt={self.condition.loop_count}), "
                    f"WP='{self.condition.input_constraint}'){expanded}")


# ---------------------------------------------------------------------------
# CCT Main Class
# ---------------------------------------------------------------------------

class CCT:
    """Condition Case Tree.

    Parameters:
        use_bounded_range: If True, when an ancestor-loop condition is met,
            additionally check whether in-range solutions exist before stopping.
        range_bound: Legacy symmetric bound used only until variable types are
            available for migration (default 200).
        input_intervals: Canonical per-variable closed integer intervals. New
            callers should provide this instead of range_bound.
        loop_occurrence_bound: Maximum condition occurrence retained before a
            path is truncated (default 2).
        debug: Enable debug printing for CSC traversal.
    """

    # Backward-compatible default alias. Runtime semantics are read from the
    # per-instance _loop_occurrence_bound field, not this class attribute.
    MAX_LOOP_BOUND = 2

    def __init__(self, use_bounded_range: bool = False,
                 range_bound: Optional[int] = None,
                 debug: bool = False, loop_occurrence_bound: int = 2,
                 input_intervals: Optional[dict] = None):
        if (isinstance(loop_occurrence_bound, bool)
                or not isinstance(loop_occurrence_bound, int)):
            raise TypeError("loop_occurrence_bound must be an integer")
        if loop_occurrence_bound < 1:
            raise ValueError("loop_occurrence_bound must be at least 1")
        if input_intervals is not None and range_bound is not None:
            raise ValueError(
                "provide either input_intervals or legacy range_bound, not both"
            )
        if not use_bounded_range and input_intervals:
            raise ValueError("original CSC mode cannot use input_intervals")
        self.root: Optional[Node] = None
        self._use_bounded_range = use_bounded_range
        if input_intervals is not None:
            self._input_intervals = validate_input_intervals(input_intervals)
            self._legacy_range_bound: Optional[int] = None
        elif use_bounded_range:
            # Compatibility state: resolve this as soon as var_types are known.
            self._input_intervals = None
            self._legacy_range_bound = 200 if range_bound is None else range_bound
            build_input_intervals_from_range_bound({}, self._legacy_range_bound)
        else:
            self._input_intervals = {}
            self._legacy_range_bound = None
        self._loop_occurrence_bound = loop_occurrence_bound
        self._debug = debug
        self.last_discovery_errors: List[dict] = []
        self.last_terminal_updates: List[dict] = []

    # ========================================================================
    # Persistence
    # ========================================================================

    def save_to_file(self, filepath: str):
        """Persist the CCT structure (root + config) to a pickle file."""
        if self.root is None:
            print("Warning: Root node is empty, no CCT to save.")
            return
        if self._input_intervals is None:
            raise ValueError(
                "Cannot save an expanded legacy CCT before migrating its "
                "range_bound with variable types"
            )
        try:
            with open(filepath, 'wb') as f:
                pickle.dump({
                    'root': self.root,
                    'use_bounded_range': self._use_bounded_range,
                    'input_intervals': self._input_intervals,
                    'loop_occurrence_bound': self._loop_occurrence_bound,
                }, f)
        except Exception as e:
            print(f"Error saving CCT: {e}")

    @staticmethod
    def load_from_file(filepath: str,
                       var_types: Optional[dict] = None) -> Optional['CCT']:
        """Load a CCT from a pickle file. Returns None if file not found.

        Handles both new format (dict with config) and old format (bare Node).
        """
        try:
            with open(filepath, 'rb') as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                root_node = data['root']
                use_bounded = data.get('use_bounded_range', False)
                loop_occurrence_bound = data.get('loop_occurrence_bound', 2)
                if 'input_intervals' in data:
                    input_intervals = data['input_intervals']
                    range_bound = None
                else:
                    input_intervals = None
                    range_bound = data.get('range_bound', 200)
            else:
                root_node = data
                use_bounded = False
                range_bound = None
                input_intervals = {}
                loop_occurrence_bound = 2
            CCT._upgrade_node(root_node)
            cct = CCT(
                use_bounded_range=use_bounded,
                range_bound=range_bound,
                loop_occurrence_bound=loop_occurrence_bound,
                input_intervals=input_intervals,
            )
            if var_types is not None:
                cct.ensure_input_intervals(var_types)
            cct.root = root_node
            return cct
        except FileNotFoundError:
            print(f"Error: File not found: {filepath}")
            return None
        except Exception as e:
            print(f"Error loading CCT: {e}")
            return None

    def ensure_input_intervals(self, var_types: dict) -> dict[str, dict[str, int]]:
        """Return canonical intervals, migrating legacy range_bound if needed."""
        if self._input_intervals is None:
            self._input_intervals = build_input_intervals_from_range_bound(
                var_types,
                self._legacy_range_bound,
            )
            self._legacy_range_bound = None
        else:
            self._input_intervals = validate_input_intervals(
                self._input_intervals,
                var_types,
            )
        return self._input_intervals

    @staticmethod
    def _upgrade_node(node: Optional['Node']):
        """Ensure old pickle-loaded nodes have new fields."""
        if node is None:
            return
        if not hasattr(node, 'is_expanded'):
            node.is_expanded = False
        if not hasattr(node, 'truncated_remainder'):
            old = getattr(node, 'truncated_condition', None)
            node.truncated_remainder = [old] if old is not None else None
        if not hasattr(node, 'truncated_remainders'):
            node.truncated_remainders = None
        if not hasattr(node, 'truncation_resolution'):
            node.truncation_resolution = None
        if not hasattr(node, 'test_inputs'):
            node.test_inputs = {} if node.is_leaf else None
        if not hasattr(node, 'tbfv_failures'):
            node.tbfv_failures = {} if node.is_leaf else None
        if not hasattr(node, 'terminal_resolution'):
            node.terminal_resolution = None
        if not hasattr(node, 'branch_resolutions'):
            node.branch_resolutions = {}
        CCT._upgrade_node(node.left)
        CCT._upgrade_node(node.right)

    def collect_stats(self) -> dict:
        """Collect CCT node statistics with one DFS traversal.

        This is intentionally an on-demand stage-boundary operation, not a
        mutation-time counter, so stats cannot drift out of sync with the tree.
        """

        stats = {
            "total_nodes": 0,
            "internal_nodes": 0,
            "leaf_nodes": 0,
            "empty_leaves": 0,
            "covered_leaves": 0,
            "valid_testcases": 0,
            "infeasible_leaves": 0,
            "out_of_range_leaves": 0,
            "truncated_leaves": 0,
            "expanded_nodes": 0,
            "expanded_internal_nodes": 0,
            "expanded_leaves": 0,
            "tbfv_fault_leaves": 0,
            "tbfv_failure_cases": 0,
            "tbfv_failure_count": 0,
            "max_depth": 0,
        }
        seen_testcases = set()
        seen_tbfv_cases = set()

        def visit(node: Optional['Node'], depth: int) -> None:
            stats["max_depth"] = max(stats["max_depth"], depth)
            if node is None:
                stats["empty_leaves"] += 1
                return

            stats["total_nodes"] += 1
            if getattr(node, "is_expanded", False):
                stats["expanded_nodes"] += 1

            if node.is_leaf:
                stats["leaf_nodes"] += 1
                if getattr(node, "is_expanded", False):
                    stats["expanded_leaves"] += 1

                test_cases = getattr(node, "test_cases", set()) or set()
                if test_cases == {INFEASIBLE_MARKER}:
                    stats["infeasible_leaves"] += 1
                elif test_cases == {RANGE_EXCLUDED_MARKER}:
                    stats["out_of_range_leaves"] += 1
                else:
                    real_cases = {
                        tc for tc in test_cases
                        if tc not in {INFEASIBLE_MARKER, RANGE_EXCLUDED_MARKER}
                    }
                    if real_cases:
                        stats["covered_leaves"] += 1
                        seen_testcases.update(real_cases)

                if self._is_truncated_leaf(node):
                    stats["truncated_leaves"] += 1

                failures = getattr(node, "tbfv_failures", None) or {}
                failed_cases = [tc for tc, entries in failures.items() if entries]
                if failed_cases:
                    stats["tbfv_fault_leaves"] += 1
                    seen_tbfv_cases.update(failed_cases)
                    stats["tbfv_failure_count"] += sum(
                        len(entries) for entries in failures.values() if entries
                    )
                return

            stats["internal_nodes"] += 1
            if getattr(node, "is_expanded", False):
                stats["expanded_internal_nodes"] += 1
            visit(node.left, depth + 1)
            visit(node.right, depth + 1)

        visit(self.root, 0)
        stats["valid_testcases"] = len(seen_testcases)
        stats["tbfv_failure_cases"] = len(seen_tbfv_cases)
        return stats

    # ========================================================================
    # Algorithm 1: Append
    # ========================================================================

    def add_sequence(self, sequence: List[ConditionResult], test_case: str,
                      is_expanded: bool = False, skip_truncation: bool = False,
                      test_inputs: dict = None):
        """Append a condition sequence to the CCT (Algorithm 1).

        Args:
            sequence: The condition sequence to append.
            test_case: Identifier for this test case.
            is_expanded: True if this node was created during expansion phase.
            skip_truncation: If True, skip loop-occurrence truncation.
            test_inputs: Optional dict of {var_name: value} for this test case.
        """
        if not sequence:
            return

        truncated_cr: Optional[ConditionResult] = None
        truncated_remainder: Optional[List[ConditionResult]] = None
        if not skip_truncation:
            truncation_index = len(sequence)
            for i, cr in enumerate(sequence):
                if cr.condition.loop_count > self._loop_occurrence_bound:
                    truncation_index = i
                    truncated_cr = sequence[i]
                    truncated_remainder = sequence[i:]
                    print(f"Path Truncation: loop count {cr.condition.loop_count} "
                          f"exceeds bound {self._loop_occurrence_bound}.")
                    break
            effective_sequence = sequence[:truncation_index]
        else:
            effective_sequence = sequence

        if not effective_sequence:
            return

        if self.root is None:
            self.root = Node(effective_sequence[0].condition, is_leaf=False)

        current = self.root
        seq_len = len(effective_sequence)

        for i in range(seq_len - 1):
            current_result = effective_sequence[i]
            next_condition = effective_sequence[i + 1].condition

            if current.is_leaf or current.condition != current_result.condition:
                print(f"Error: Sequence mismatch at step {i + 1}")
                return

            if not current_result.result:
                if current.left is None:
                    current.left = Node(next_condition, is_leaf=False)
                current = current.left
            else:
                if current.right is None:
                    current.right = Node(next_condition, is_leaf=False)
                current = current.right

        last_result = effective_sequence[-1]
        if current.is_leaf or current.condition != last_result.condition:
            print(f"Error: Final sequence mismatch")
            return

        if not last_result.result:
            if current.left is None:
                leaf = Node(test_case, is_leaf=True)
                leaf.is_expanded = is_expanded
                leaf.truncated_remainder = truncated_remainder
                if truncated_remainder is not None:
                    leaf.truncated_remainders = {test_case: truncated_remainder}
                if test_inputs is not None:
                    leaf.test_inputs = {test_case: test_inputs}
                current.left = leaf
            elif current.left.is_leaf:
                current.left.add_test_case(test_case, test_inputs)
                if truncated_remainder is not None:
                    self._store_truncated_remainder(current.left, test_case, truncated_remainder)
            elif getattr(current.left, 'is_expanded', False):
                self._add_to_expanded_leaf(current.left, test_case, truncated_cr, truncated_remainder, test_inputs)
            else:
                print("Error: Expected leaf, found internal node in F branch.")
        else:
            if current.right is None:
                leaf = Node(test_case, is_leaf=True)
                leaf.is_expanded = is_expanded
                leaf.truncated_remainder = truncated_remainder
                if truncated_remainder is not None:
                    leaf.truncated_remainders = {test_case: truncated_remainder}
                if test_inputs is not None:
                    leaf.test_inputs = {test_case: test_inputs}
                current.right = leaf
            elif current.right.is_leaf:
                current.right.add_test_case(test_case, test_inputs)
                if truncated_remainder is not None:
                    self._store_truncated_remainder(current.right, test_case, truncated_remainder)
            elif getattr(current.right, 'is_expanded', False):
                self._add_to_expanded_leaf(current.right, test_case, truncated_cr, truncated_remainder, test_inputs)
            else:
                print("Error: Expected leaf, found internal node in T branch.")

    # ========================================================================
    # TBFV failure annotations
    # ========================================================================

    def mark_tbfv_failure(self, test_case_id: str, fsf_id: str,
                          counterexample: dict, formula: str,
                          reason: str = "",
                          T: str = "", D: str = "") -> bool:
        """Attach refined-TBFV failure metadata to a covered leaf.

        This method is intentionally an annotation API: it does not change CCT
        topology and does not reuse CSC exploration markers.
        """
        leaf = self._find_leaf_by_test_case(self.root, test_case_id)
        if leaf is None:
            return False
        if not hasattr(leaf, 'tbfv_failures') or leaf.tbfv_failures is None:
            leaf.tbfv_failures = {}
        entry = {
            "fsf_id": fsf_id,
            "counterexample": counterexample,
            "formula": formula,
            "reason": reason,
        }
        if T:
            entry["T"] = T
        if D:
            entry["D"] = D
        leaf.tbfv_failures.setdefault(test_case_id, []).append(entry)
        return True

    def _find_leaf_by_test_case(self, node: Optional[Node],
                                test_case_id: str) -> Optional[Node]:
        if node is None:
            return None
        if node.is_leaf:
            if node.test_cases and test_case_id in node.test_cases:
                return node
            return None
        return (self._find_leaf_by_test_case(node.left, test_case_id)
                or self._find_leaf_by_test_case(node.right, test_case_id))

    # ========================================================================
    # Mark infeasible
    # ========================================================================

    def record_branch_resolution(self, sequence: List[ConditionResult],
                                 resolution: dict) -> bool:
        """Annotate an open branch without changing its coverage status."""
        if not sequence or self.root is None:
            return False
        current = self.root
        for cr in sequence[:-1]:
            if current is None or current.is_leaf:
                return False
            current = current.right if cr.result else current.left
        if current is None or current.is_leaf:
            return False
        side = sequence[-1].result
        if (current.right if side else current.left) is not None:
            return False
        current.branch_resolutions[side] = dict(resolution)
        return True

    def mark_infeasible(self, sequence: List[ConditionResult],
                        resolution: Optional[dict] = None):
        """Mark a target branch as Infeasible ('X')."""
        if not sequence or self.root is None:
            return

        current = self.root
        for cr in sequence[:-1]:
            if not cr.result:
                current = current.left
            else:
                current = current.right
            if current is None:
                print("Error: Path structure lost during mark_infeasible.")
                return

        if current.is_leaf:
            print("Error: Target node is not an internal node.")
            return

        last_result = sequence[-1]
        terminal = Node(INFEASIBLE_MARKER, is_leaf=True)
        terminal.terminal_resolution = dict(resolution) if resolution else None
        if not last_result.result:
            current.left = terminal
        else:
            current.right = terminal

    def apply_terminal_update(self, path: List[bool], target_side: bool,
                              marker: str, resolution: Optional[dict] = None) -> str:
        """Apply a keyed terminal update without overwriting concrete evidence.

        Returns ``applied`` for a new marker, ``idempotent`` when the same
        marker is already present, and ``incompatible`` when the target no
        longer denotes an unallocated branch.
        """
        if marker not in {INFEASIBLE_MARKER, RANGE_EXCLUDED_MARKER}:
            return "incompatible"

        current = self.root
        for side in path:
            if current is None or current.is_leaf:
                return "incompatible"
            current = current.right if side else current.left

        if current is None or current.is_leaf:
            return "incompatible"

        existing = current.right if target_side else current.left
        if existing is None:
            terminal = Node(marker, is_leaf=True)
            terminal.terminal_resolution = dict(resolution) if resolution else None
            if target_side:
                current.right = terminal
            else:
                current.left = terminal
            return "applied"
        if existing.is_leaf and existing.test_cases == {marker}:
            if resolution and getattr(existing, "terminal_resolution", None) is None:
                existing.terminal_resolution = dict(resolution)
            return "idempotent"
        return "incompatible"

    @staticmethod
    def _store_truncated_remainder(leaf: 'Node', test_case: str,
                                    remainder: List['ConditionResult']):
        """Lazily init truncated_remainders dict and store this test case's remainder."""
        if leaf.truncated_remainders is None:
            leaf.truncated_remainders = {}
            for tc in leaf.test_cases:
                if tc != test_case:
                    leaf.truncated_remainders[tc] = leaf.truncated_remainder
        leaf.truncated_remainders[test_case] = remainder

    def _add_to_expanded_leaf(self, expanded_node: Node, test_case: str,
                               truncated_cr: Optional['ConditionResult'] = None,
                               truncated_remainder: Optional[List['ConditionResult']] = None,
                               test_inputs: dict = None):
        """Add a test case into the correct side of an expanded node."""
        use_right = (truncated_cr.result if truncated_cr else True)
        target_child = expanded_node.right if use_right else expanded_node.left

        if target_child is not None:
            if target_child.is_leaf:
                if target_child.test_cases != {INFEASIBLE_MARKER}:
                    target_child.add_test_case(test_case, test_inputs)
                    return
            elif (getattr(target_child, 'is_expanded', False) and
                  truncated_remainder and len(truncated_remainder) > 1):
                next_cr = truncated_remainder[1]
                next_remainder = truncated_remainder[1:]
                self._add_to_expanded_leaf(target_child, test_case,
                                           next_cr, next_remainder, test_inputs)
                return

        if target_child is None:
            leaf = Node(test_case, is_leaf=True)
            leaf.is_expanded = True
            if truncated_remainder and len(truncated_remainder) > 1:
                leaf.truncated_remainder = truncated_remainder[1:]
                leaf.truncated_remainders = {test_case: truncated_remainder[1:]}
            if test_inputs is not None:
                leaf.test_inputs = {test_case: test_inputs}
            if use_right:
                expanded_node.right = leaf
            else:
                expanded_node.left = leaf

    # ========================================================================
    # Leaf classification helpers
    # ========================================================================

    def _is_range_excluded_leaf(self, node: Optional[Node]) -> bool:
        return node is not None and node.is_leaf and node.test_cases == {RANGE_EXCLUDED_MARKER}

    def _is_infeasible_leaf(self, node: Optional[Node]) -> bool:
        return node is not None and node.is_leaf and node.test_cases == {INFEASIBLE_MARKER}

    def _is_truncated_leaf(self, node: Optional[Node]) -> bool:
        return (node is not None and node.is_leaf
                and node.truncated_remainder is not None
                and len(node.truncated_remainder) > 0
                and not self._is_infeasible_leaf(node))

    # ========================================================================
    # Expansion: truncated leaf promotion
    # ========================================================================

    def _promote_truncated_leaf(self, parent: Node, is_right_child: bool):
        """Convert a truncated leaf into an internal node, splitting test cases correctly.

        Uses per-test-case truncated remainders (truncated_remainders dict) to place
        each test case on the correct side, rather than assuming all share the same
        first remainder result.
        """
        leaf = parent.right if is_right_child else parent.left
        remainder = leaf.truncated_remainder
        first_cr = remainder[0]
        rest = remainder[1:] if len(remainder) > 1 else None
        old_test_cases = list(leaf.test_cases) if leaf.test_cases else []
        remainders = getattr(leaf, 'truncated_remainders', None) or {}

        new_internal = Node(first_cr.condition, is_leaf=False)
        new_internal.is_expanded = True

        # Group test cases by their first remainder result
        true_cases = []
        false_cases = []
        for tc in old_test_cases:
            tc_remainder = remainders.get(tc, remainder)
            if tc_remainder and tc_remainder[0].result:
                true_cases.append(tc)
            else:
                false_cases.append(tc)

        # Create right child (True branch)
        if true_cases:
            rehomed_right = Node(true_cases[0], is_leaf=True)
            rehomed_right.is_expanded = True
            first_true_r = remainders.get(true_cases[0], remainder)
            right_rest = first_true_r[1:] if len(first_true_r) > 1 else None
            rehomed_right.truncated_remainder = right_rest
            if right_rest:
                rehomed_right.truncated_remainders = {
                    tc: remainders.get(tc, remainder)[1:]
                    for tc in true_cases
                }
            # Propagate test_inputs
            if hasattr(leaf, 'test_inputs') and leaf.test_inputs:
                rehomed_right.test_inputs = {}
                for tc in true_cases:
                    if tc in leaf.test_inputs:
                        rehomed_right.test_inputs[tc] = leaf.test_inputs[tc]
            for tc in true_cases[1:]:
                rehomed_right.add_test_case(tc)
            new_internal.right = rehomed_right

        # Create left child (False branch)
        if false_cases:
            rehomed_left = Node(false_cases[0], is_leaf=True)
            rehomed_left.is_expanded = True
            first_false_r = remainders.get(false_cases[0], remainder)
            left_rest = first_false_r[1:] if len(first_false_r) > 1 else None
            rehomed_left.truncated_remainder = left_rest
            if left_rest:
                rehomed_left.truncated_remainders = {
                    tc: remainders.get(tc, remainder)[1:]
                    for tc in false_cases
                }
            # Propagate test_inputs
            if hasattr(leaf, 'test_inputs') and leaf.test_inputs:
                rehomed_left.test_inputs = {}
                for tc in false_cases:
                    if tc in leaf.test_inputs:
                        rehomed_left.test_inputs[tc] = leaf.test_inputs[tc]
            for tc in false_cases[1:]:
                rehomed_left.add_test_case(tc)
            new_internal.left = rehomed_left

        if is_right_child:
            parent.right = new_internal
        else:
            parent.left = new_internal

        children_desc = []
        if true_cases:
            children_desc.append(f"T:{true_cases}")
        if false_cases:
            children_desc.append(f"F:{false_cases}")
        print(f"  [EXPAND] Promoted truncated leaf at '{first_cr.condition.condition_string}' "
              f"(cnt={first_cr.condition.loop_count}) → "
              f"{' | '.join(children_desc)}")

    # ========================================================================
    # Expansion: ancestor check
    # ========================================================================

    def _node_has_nonempty_child(self, node: Node, is_right_branch: bool) -> bool:
        """Check whether the side has a concrete continuation."""
        child = node.right if is_right_branch else node.left
        return (child is not None
                and not self._is_infeasible_leaf(child)
                and not self._is_range_excluded_leaf(child))

    def _has_ancestor_with_same_condition(self, path_to_node: List[ConditionResult],
                                           node_condition_string: str,
                                           is_right_branch: bool,
                                           line_number: int = None) -> bool:
        """Check if any ancestor has the same condition with a non-empty child on the target side.

        Implements the paper's Condition 1 ancestor check. The node at the end
        of path_to_node is the target itself, NOT an ancestor — we only check
        root and intermediate nodes.
        """
        if self.root is None:
            return False

        current = self.root

        # Check root first (always an ancestor of any node)
        if (not current.is_leaf and
            current.condition.condition_string == node_condition_string and
            (line_number is None or current.condition.line_number == line_number) and
            self._node_has_nonempty_child(current, is_right_branch)):
            return True

        for i, cr in enumerate(path_to_node):
            current = current.right if cr.result else current.left
            if current is None or current.is_leaf:
                return False

            if i == len(path_to_node) - 1:
                continue  # skip the target node itself

            if (current.condition.condition_string == node_condition_string and
                (line_number is None or current.condition.line_number == line_number) and
                self._node_has_nonempty_child(current, is_right_branch)):
                return True

        return False

    # ========================================================================
    # Expansion: range satisfiability checks
    # ========================================================================

    def _check_bounded_range(self, sequence: List[ConditionResult],
                             var_types: dict) -> SolverResult:
        """Preserve the exact result of a bounded path check."""
        new_path = self.construct_path_constraint(sequence)
        new_path = add_input_interval_constraints(
            new_path,
            var_types,
            self.ensure_input_intervals(var_types),
        )
        try:
            z3_expr = java_expr_to_z3(new_path, var_types)
            return solver_check_z3_structured(z3_expr, var_types)
        except Exception as e:
            print(f"Range satisfiability check error: {e}")
            return SolverResult(kind="error", reason=str(e))

    def _check_globally(self, sequence: List[ConditionResult],
                        var_types: dict) -> SolverResult:
        """Preserve the exact result of a Java-values path check."""
        new_path = self.construct_path_constraint(sequence)
        new_path = add_value_constraints(new_path, var_types)
        try:
            z3_expr = java_expr_to_z3(new_path, var_types)
            return solver_check_z3_structured(z3_expr, var_types)
        except Exception as e:
            print(f"Global satisfiability check error: {e}")
            return SolverResult(kind="error", reason=str(e))

    # ========================================================================
    # Algorithm 2: Check for CSC
    # ========================================================================

    def check_for_csc(self, var_types: dict = None) -> Optional[List[ConditionResult]]:
        """Find the first uncovered branch (DFS, left-first).

        With expansion enabled, branches under ancestor-loop conditions are
        only skipped if no in-range solutions exist.
        """
        if self.root is None:
            if self._debug:
                print("  [DEBUG] CCT root is None -> NOT_INITIALIZED")
            return None

        var_types = var_types or {}
        if self._use_bounded_range:
            self.ensure_input_intervals(var_types)

        if self._debug:
            mode = (
                f"input_intervals={self._input_intervals}"
                if self._use_bounded_range else "original CSC"
            )
            print(f"\n  [DEBUG] check_for_csc() — mode={mode}")

        return self._check_recursive(self.root, [], var_types)

    def _check_recursive(self, node: Optional[Node],
                          current_sequence: List[ConditionResult],
                          var_types: dict) -> Optional[List[ConditionResult]]:
        """Recursive DFS for Algorithm 2 with expansion support."""
        if node is None:
            return None

        # --- F (False) Branch ---
        if node.left is None:
            result = self._handle_uncovered_branch(
                node, current_sequence, is_right_branch=False, var_types=var_types)
            if result is not None:
                return result

        elif self._is_truncated_leaf(node.left):
            if self._debug:
                print(f"  [DEBUG] Truncated leaf at '{node.condition.condition_string}' F branch")
            if self._use_bounded_range:
                result = self._try_expand_truncated(
                    node, is_right_child=False, current_sequence=current_sequence,
                    var_types=var_types)
                if result is not None:
                    return result

        elif not self._is_infeasible_leaf(node.left) and not node.left.is_leaf:
            new_sequence = current_sequence + [ConditionResult(node.condition, False)]
            result = self._check_recursive(node.left, new_sequence, var_types)
            if result is not None:
                return result

        # --- T (True) Branch ---
        if node.right is None:
            result = self._handle_uncovered_branch(
                node, current_sequence, is_right_branch=True, var_types=var_types)
            if result is not None:
                return result

        elif self._is_truncated_leaf(node.right):
            if self._debug:
                print(f"  [DEBUG] Truncated leaf at '{node.condition.condition_string}' T branch")
            if self._use_bounded_range:
                result = self._try_expand_truncated(
                    node, is_right_child=True, current_sequence=current_sequence,
                    var_types=var_types)
                if result is not None:
                    return result

        elif not self._is_infeasible_leaf(node.right) and not node.right.is_leaf:
            new_sequence = current_sequence + [ConditionResult(node.condition, True)]
            result = self._check_recursive(node.right, new_sequence, var_types)
            if result is not None:
                return result

        return None

    # ========================================================================
    # Batch: discover all uncovered branches
    # ========================================================================

    def discover_all_uncovered(self, var_types: dict) -> List[dict]:
        """Walk CCT, find ALL uncovered branches, Z3-solve for inputs.

        Returns a list of dicts with:
            idx: sequential index for correlation with batch verify
            inputs: dict of var_name -> value from Z3 model
            debug_path: human-readable path description
        """
        results = []
        self.last_discovery_errors = []
        self.last_terminal_updates = []
        if self._use_bounded_range:
            self.ensure_input_intervals(var_types)

        def _branch_key(path_conds, target_side):
            outcomes = [is_right for _, is_right in path_conds] + [target_side]
            return "".join("T" if outcome else "F" for outcome in outcomes)

        def _queue_terminal(node, path_conds, target_side, marker, resolution):
            self.last_terminal_updates.append({
                "branch_id": _branch_key(path_conds, target_side),
                "path": [is_right for _, is_right in path_conds],
                "target_side": target_side,
                "marker": marker,
                "condition": node.condition.condition_string,
                "line_number": node.condition.line_number,
                "resolution": resolution,
            })

        def _record_failure(node, path_conds, target_side, result, path_constraint):
            kind = "solver_unknown" if result.kind == "unknown" else "solver_error"
            node.branch_resolutions[target_side] = {
                "kind": kind, "solverResult": result.kind,
            }
            self.last_discovery_errors.append({
                "branch_id": _branch_key(path_conds, target_side),
                "condition": node.condition.condition_string,
                "side": "T" if target_side else "F",
                "path_constraint": path_constraint,
                "error": result.reason or result.kind,
                "solver_result": result.kind,
            })

        def _serialize_path(path_conds, target_side):
            parts = []
            for cond_str, is_right in path_conds:
                side = "T" if is_right else "F"
                parts.append(f"{cond_str}@{side}")
            parts.append(target_side)
            return " > ".join(parts)

        def _collect_branch(node, current_sequence, path_conds,
                            is_right_branch, var_types):
            cond_str = node.condition.condition_string
            side_label = "T" if is_right_branch else "F"

            has_ancestor = self._has_ancestor_with_same_condition(
                current_sequence, cond_str, is_right_branch, node.condition.line_number)

            target_sequence = current_sequence + [
                ConditionResult(node.condition, is_right_branch)]

            if has_ancestor and not self._use_bounded_range:
                node.branch_resolutions[is_right_branch] = {"kind": "ancestor_stopped"}
                if self._debug:
                    print(f"  [BATCH] Skip '{cond_str}'→{side_label} (ancestor match, no expansion)")
                return

            if has_ancestor and self._use_bounded_range:
                bounded = self._check_bounded_range(target_sequence, var_types)
                if bounded.kind not in {"sat", "unsat"}:
                    _record_failure(node, path_conds, is_right_branch,
                                    bounded, self.construct_path_constraint(target_sequence))
                    return
                if bounded.kind == "unsat":
                    global_result = self._check_globally(target_sequence, var_types)
                    if global_result.kind not in {"sat", "unsat"}:
                        _record_failure(node, path_conds, is_right_branch,
                                        global_result, self.construct_path_constraint(target_sequence))
                        return
                    if global_result.kind == "sat":
                        _queue_terminal(node, path_conds, is_right_branch,
                                        RANGE_EXCLUDED_MARKER, {
                                            "kind": "range_excluded", "boundedResult": "unsat",
                                            "globalResult": "sat",
                                        })
                    else:
                        _queue_terminal(node, path_conds, is_right_branch,
                                        INFEASIBLE_MARKER, {
                                            "kind": "solver_unsat", "solverResult": "unsat",
                                            "scope": "java_values",
                                        })
                    return

            path_constraint = self.construct_path_constraint(target_sequence)
            if has_ancestor and self._use_bounded_range:
                path_constraint_with_values = add_input_interval_constraints(
                    path_constraint,
                    var_types,
                    self.ensure_input_intervals(var_types),
                )
            else:
                path_constraint_with_values = add_value_constraints(path_constraint, var_types)
            try:
                solver_start = time.perf_counter()
                z3_expr = java_expr_to_z3(path_constraint_with_values, var_types)
                solver_result = solver_check_z3_structured(z3_expr, var_types)
                solver_time_ms = int((time.perf_counter() - solver_start) * 1000)
                if solver_result.kind == "sat":
                    inputs = parse_result(solver_result.model)
                    debug_path = _serialize_path(path_conds + [(cond_str, is_right_branch)], "")
                    results.append({
                        "idx": len(results),
                        "branch_id": _branch_key(path_conds, is_right_branch),
                        "inputs": inputs,
                        "debug_path": debug_path,
                        "path_constraint": path_constraint,
                        "solver_time_ms": solver_time_ms,
                    })
                    if self._debug:
                        print(f"  [BATCH] Found branch #{len(results)-1}: {debug_path} -> {inputs}")
                elif solver_result.kind == "unsat" and not has_ancestor:
                    if self._debug:
                        print(f"  [BATCH] UNSAT for '{cond_str}'→{side_label}, marking Infeasible")
                    _queue_terminal(
                        node, path_conds, is_right_branch,
                        INFEASIBLE_MARKER, {
                            "kind": "solver_unsat", "solverResult": "unsat",
                            "scope": "java_values",
                        })
                else:
                    _record_failure(node, path_conds, is_right_branch,
                                    SolverResult(kind="error", reason=
                                                 "bounded check changed from SAT to UNSAT")
                                    if solver_result.kind == "unsat" else solver_result,
                                    path_constraint)
            except Exception as e:
                _record_failure(node, path_conds, is_right_branch,
                                SolverResult(kind="error", reason=str(e)), path_constraint)
                print(f"  [BATCH] Error during Z3 solve for branch '{cond_str}'→{side_label}: {e}")

        def _walk(node, current_sequence, path_conds):
            if node is None:
                return
            if node.is_leaf:
                if self._is_truncated_leaf(node):
                    pass
                return

            if node.left is None:
                _collect_branch(node, current_sequence, path_conds,
                               is_right_branch=False, var_types=var_types)
            elif self._is_truncated_leaf(node.left):
                if self._use_bounded_range:
                    self._try_expand_truncated(node, is_right_child=False,
                                               current_sequence=current_sequence,
                                               var_types=var_types)
                    if node.left is not None and not node.left.is_leaf:
                        new_seq = current_sequence + [ConditionResult(node.condition, False)]
                        new_path = path_conds + [(node.condition.condition_string, False)]
                        _walk(node.left, new_seq, new_path)
            elif not self._is_infeasible_leaf(node.left) and not self._is_range_excluded_leaf(node.left) and not node.left.is_leaf:
                new_seq = current_sequence + [ConditionResult(node.condition, False)]
                new_path = path_conds + [(node.condition.condition_string, False)]
                _walk(node.left, new_seq, new_path)

            if node.right is None:
                _collect_branch(node, current_sequence, path_conds,
                               is_right_branch=True, var_types=var_types)
            elif self._is_truncated_leaf(node.right):
                if self._use_bounded_range:
                    self._try_expand_truncated(node, is_right_child=True,
                                               current_sequence=current_sequence,
                                               var_types=var_types)
                    if node.right is not None and not node.right.is_leaf:
                        new_seq = current_sequence + [ConditionResult(node.condition, True)]
                        new_path = path_conds + [(node.condition.condition_string, True)]
                        _walk(node.right, new_seq, new_path)
            elif not self._is_infeasible_leaf(node.right) and not self._is_range_excluded_leaf(node.right) and not node.right.is_leaf:
                new_seq = current_sequence + [ConditionResult(node.condition, True)]
                new_path = path_conds + [(node.condition.condition_string, True)]
                _walk(node.right, new_seq, new_path)

        if self.root is None:
            return results

        _walk(self.root, [], [])
        return results

    def _try_expand_truncated(self, parent: Node, is_right_child: bool,
                               current_sequence: List[ConditionResult],
                               var_types: dict) -> Optional[List[ConditionResult]]:
        """Try to expand a truncated leaf. Returns target sequence if SAT, None if not."""
        leaf = parent.right if is_right_child else parent.left
        truncated_cr = leaf.truncated_remainder[0]

        is_right_branch = is_right_child
        side_cr = ConditionResult(parent.condition, is_right_branch)
        prefix_sequence = current_sequence + [side_cr]

        if self._debug:
            path_constr = self.construct_path_constraint(prefix_sequence)
            bounded = add_input_interval_constraints(
                path_constr,
                var_types,
                self.ensure_input_intervals(var_types),
            )
            print(f"  [DEBUG] Trying to expand truncated leaf -> '{truncated_cr.condition.condition_string}'")
            print(f"          Path prefix: {path_constr}")
            print(f"          + bounded range:  {bounded}")

        bounded_result = self._check_bounded_range(prefix_sequence, var_types)
        if bounded_result.kind not in {"sat", "unsat"}:
            return None
        if bounded_result.kind == "sat":
            if self._debug:
                print(f"          -> SAT: promoting truncated leaf to internal node")
            self._promote_truncated_leaf(parent, is_right_child)
            new_node = parent.right if is_right_child else parent.left
            return self._check_recursive(new_node, prefix_sequence, var_types)
        else:
            global_result = self._check_globally(prefix_sequence, var_types)
            if global_result.kind not in {"sat", "unsat"}:
                return None
            if global_result.kind == "sat":
                # Preserve the original CCT representation of a Boundary stop.
                # Executed test records and traces remain in generation artifacts;
                # this collapsed CCT leaf represents the range-excluded suffix.
                leaf.test_cases = {RANGE_EXCLUDED_MARKER}
                leaf.test_inputs = None
                leaf.terminal_resolution = {
                    "kind": "range_excluded", "boundedResult": "unsat",
                    "globalResult": "sat",
                }
                leaf.truncation_resolution = None
                debug_reason = "no in-range prefix continuation"
                leaf.truncated_remainder = None
                leaf.truncated_remainders = None
            else:
                # A global-UNSAT result contradicts the concrete execution
                # already stored at this leaf.  Preserve that stronger
                # evidence and keep the truncation unresolved so downstream
                # outcome projection remains conservative.
                leaf.truncation_resolution = {
                    "kind": "solver_error", "solverResult": "error",
                    "reason": "global UNSAT contradicts a concrete test",
                }
                debug_reason = "global UNSAT contradicted concrete evidence"
            if self._debug:
                print(f"          -> Chain terminated ({debug_reason})")
            return None

    def _handle_uncovered_branch(self, node: Node,
                                  current_sequence: List[ConditionResult],
                                  is_right_branch: bool,
                                  var_types: dict) -> Optional[List[ConditionResult]]:
        """Decide whether to explore or skip an uncovered (None) branch.

        Logic:
          1. No matching ancestor -> always explore.
          2. Matching ancestor + use_bounded_range OFF -> skip (original CSC).
          3. Matching ancestor + use_bounded_range ON -> range check:
             SAT -> explore, UNSAT -> skip (mark infeasible or out-of-range).
        """
        branch_label = "T (right)" if is_right_branch else "F (left)"
        cond_str = node.condition.condition_string

        if self._debug:
            print(f"  [DEBUG] Uncovered branch: '{cond_str}' -> {branch_label}")
            print(f"          Path: {[(cr.condition.condition_string, 'T' if cr.result else 'F') for cr in current_sequence]}")

        has_ancestor = self._has_ancestor_with_same_condition(
            current_sequence, node.condition.condition_string, is_right_branch,
            node.condition.line_number)

        if self._debug:
            print(f"          Ancestor match? {'YES' if has_ancestor else 'NO'}")

        if not has_ancestor:
            if self._debug:
                print(f"          -> EXPLORE (no matching ancestor)")
            return current_sequence + [ConditionResult(node.condition, is_right_branch)]

        if not self._use_bounded_range:
            node.branch_resolutions[is_right_branch] = {"kind": "ancestor_stopped"}
            if self._debug:
                print(f"          -> SKIP (original CSC: Condition 1)")
            return None

        target_sequence = current_sequence + [ConditionResult(node.condition, is_right_branch)]

        path_constraint = self.construct_path_constraint(target_sequence)
        bounded_constraint = add_input_interval_constraints(
            path_constraint,
            var_types,
            self.ensure_input_intervals(var_types),
        )

        if self._debug:
            print(f"          Input intervals ON {self._input_intervals}")
            print(f"          Path constraint: {path_constraint}")
            print(f"          + range:        {bounded_constraint}")

        bounded_result = self._check_bounded_range(target_sequence, var_types)
        if bounded_result.kind not in {"sat", "unsat"}:
            node.branch_resolutions[is_right_branch] = {
                "kind": "solver_unknown" if bounded_result.kind == "unknown" else "solver_error",
                "solverResult": bounded_result.kind,
            }
            return None
        if bounded_result.kind == "sat":
            if self._debug:
                print(f"          -> EXPLORE (SAT: in-range solutions exist)")
            return target_sequence
        else:
            global_result = self._check_globally(target_sequence, var_types)
            if global_result.kind not in {"sat", "unsat"}:
                node.branch_resolutions[is_right_branch] = {
                    "kind": "solver_unknown" if global_result.kind == "unknown" else "solver_error",
                    "solverResult": global_result.kind,
                }
                return None
            if global_result.kind == "sat":
                marker = Node(RANGE_EXCLUDED_MARKER, is_leaf=True)
                marker.terminal_resolution = {
                    "kind": "range_excluded", "boundedResult": "unsat",
                    "globalResult": "sat",
                }
                if is_right_branch:
                    node.right = marker
                else:
                    node.left = marker
                if self._debug:
                    print("          -> SKIP (Out-of-Range: SAT outside input intervals)")
            else:
                marker = Node(INFEASIBLE_MARKER, is_leaf=True)
                marker.terminal_resolution = {
                    "kind": "solver_unsat", "solverResult": "unsat",
                    "scope": "java_values",
                }
                if is_right_branch:
                    node.right = marker
                else:
                    node.left = marker
                if self._debug:
                    print(f"          -> SKIP (Infeasible: globally UNSAT)")
            return None

    # ========================================================================
    # Path constraint construction
    # ========================================================================

    def construct_path_constraint(self, sequence: List[ConditionResult]) -> str:
        """Build the Path Constraint (PC) from a condition sequence.

        Uses WP-derived input constraints. For each condition:
        - True -> (predicate)
        - False -> (!(predicate))
        """
        constraints = []
        for cr in sequence:
            predicate = cr.condition.input_constraint.strip()
            if predicate.startswith("(") and predicate.endswith(")"):
                predicate = predicate[1:-1]

            if cr.result:
                constraints.append(f"({predicate})")
            else:
                constraints.append(f"(!({predicate}))")

        if not constraints:
            return "True (Empty Path Constraint)"

        return " && ".join(constraints)

    # ========================================================================
    # Display and Visualization
    # ========================================================================

    def print_tree(self):
        """Print the CCT structure to stdout (text fallback)."""
        print("\n--- Current CCT Structure ---")
        print(f"  [Config: use_bounded_range={self._use_bounded_range}, "
              f"input_intervals={self._input_intervals}]")
        self._print_recursive(self.root, 0)
        print("-----------------------------\n")

    def _print_recursive(self, node: Optional[Node], level: int):
        if node is None:
            print("  |   " * level + "{Ø}")
            return

        indent = "  |   " * level
        if node.is_leaf:
            if node.test_cases == {INFEASIBLE_MARKER}:
                print(indent + "[LEAF] Infeasible (X)")
            elif node.test_cases == {RANGE_EXCLUDED_MARKER}:
                print(indent + "[LEAF] Out-of-Range")
            else:
                flags = []
                if node.truncated_remainder is not None and len(node.truncated_remainder) > 0:
                    flags.append("TRUNCATED")
                if node.is_expanded:
                    flags.append("EXPANDED")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                inputs_str = ""
                if node.test_inputs:
                    parts = []
                    for tc in sorted(node.test_cases):
                        if tc in node.test_inputs:
                            inp = node.test_inputs[tc]
                            inp_parts = [f"{k}={v}" for k, v in inp.items()]
                            parts.append(f"{tc}({', '.join(inp_parts)})")
                    if parts:
                        inputs_str = "  inputs: " + " | ".join(parts)
                print(indent + f"[LEAF] Cases: {node.test_cases}{flag_str}{inputs_str}")
        else:
            expanded = " [EXPANDED]" if node.is_expanded else ""
            print(indent + f"[NODE] Condition: {node.condition.condition_string} "
                  f"@L{node.condition.line_number} "
                  f"(WP: {node.condition.input_constraint}, "
                  f"Cnt: {node.condition.loop_count}){expanded}")
            print("  |   " * level + "  (F) ->")
            self._print_recursive(node.left, level + 1)
            print("  |   " * level + "  (T) ->")
            self._print_recursive(node.right, level + 1)

    def to_dot(self, name: str = "CCT") -> str:
        """Generate a Graphviz DOT representation of the CCT.

        Render with: dot -Tpng cct.dot -o cct.png

        Color coding:
          Green  = Covered (has test cases)
          Gray   = Infeasible (X)
          Yellow = Out-of-Range (no in-range solution)
          Orange = Truncated (loop occurrence bound)
          Blue   = Expanded node
          Gray   = Uncovered (empty)
        """
        lines = ['digraph ' + name + ' {',
                 '  rankdir=TB;',
                 '  node [shape=box, style=filled, fontname="monospace", fontsize=10];',
                 '  edge [fontname="monospace", fontsize=9];',
                 '',
                 '  // Legend',
                 '  {',
                 '    rank=sink;',
                 '    legend [shape=plaintext, label=<',
                 '      <table border="0" cellborder="1" cellspacing="0" cellpadding="4">',
                 '        <tr><td bgcolor="#e8f5e9"><b>Leaf</b></td><td>Covered (has test cases)</td></tr>',
                 '        <tr><td bgcolor="#d6d6d6"><b>Leaf</b></td><td>Infeasible (X)</td></tr>',
                 '        <tr><td bgcolor="#fff9c4"><b>Leaf</b></td><td>Out-of-Range</td></tr>',
                 '        <tr><td bgcolor="#fff3e0"><b>Leaf</b></td><td>Truncated (loop occurrence bound)</td></tr>',
                 '        <tr><td bgcolor="#e3f2fd"><b>Leaf</b></td><td>Expanded node</td></tr>',
                 '        <tr><td bgcolor="#eeeeee"><b>Leaf Ø</b></td><td>Uncovered (empty)</td></tr>',
                 '      </table>',
                 '    >];',
                 '  }',
                 '']
        self._counter = 0
        self._dot_recursive(self.root, lines, is_right=None, parent_id=None)
        lines.append('}')
        return '\n'.join(lines)

    def to_tbfv_fault_dot(self, name: str = "CCT_TBFV_Faults") -> str:
        """Generate a separate DOT view for refined-TBFV failures."""
        lines = ['digraph ' + name + ' {',
                 '  rankdir=TB;',
                 '  node [shape=box, style=filled, fontname="monospace", fontsize=10];',
                 '  edge [fontname="monospace", fontsize=9];',
                 '',
                 '  // Legend',
                 '  {',
                 '    rank=sink;',
                 '    legend [shape=plaintext, label=<',
                 '      <table border="0" cellborder="1" cellspacing="0" cellpadding="4">',
                 '        <tr><td bgcolor="#ffcdd2"><b>Leaf</b></td><td>TBFV failure</td></tr>',
                 '        <tr><td bgcolor="#e8f5e9"><b>Leaf</b></td><td>No TBFV failure</td></tr>',
                 '        <tr><td bgcolor="#d6d6d6"><b>Leaf</b></td><td>Infeasible (context)</td></tr>',
                 '        <tr><td bgcolor="#fff9c4"><b>Leaf</b></td><td>Out-of-Range (context)</td></tr>',
                 '        <tr><td bgcolor="#eeeeee"><b>Leaf Ø</b></td><td>Uncovered</td></tr>',
                 '      </table>',
                 '    >];',
                 '  }',
                 '']
        self._counter = 0
        self._dot_fault_recursive(self.root, lines, is_right=None, parent_id=None)
        lines.append('}')
        return '\n'.join(lines)

    def _dot_recursive(self, node: Optional[Node], lines: List[str],
                       is_right: Optional[bool], parent_id: Optional[str]):
        node_id = f"node{self._counter}"
        self._counter += 1

        if node is None:
            label = "{Ø}"
            lines.append(f'  {node_id} [label="{label}", shape=ellipse, '
                         f'fillcolor="#eeeeee", fontcolor="#999999", style=dashed];')
        elif node.is_leaf:
            if node.test_cases == {INFEASIBLE_MARKER}:
                label = "✗ INFEASIBLE"
                color = "#d6d6d6"
                font = "#555555"
            elif node.test_cases == {RANGE_EXCLUDED_MARKER}:
                label = "↯ OUT-OF-RANGE"
                color = "#fff9c4"
                font = "#f57f17"
            else:
                sorted_cases = sorted(list(node.test_cases))
                label_parts = []
                for tc in sorted_cases[:5]:
                    if node.test_inputs and tc in node.test_inputs:
                        inp = node.test_inputs[tc]
                        inp_str = ", ".join(f"{k}={v}" for k, v in inp.items())
                        label_parts.append(f"{tc}({inp_str})")
                    else:
                        label_parts.append(tc)
                label = ", ".join(label_parts)
                more = f" ... (+{len(sorted_cases) - 5})" if len(sorted_cases) > 5 else ""
                label += more
                if node.is_expanded:
                    color = "#e3f2fd"
                elif node.truncated_remainder:
                    color = "#fff3e0"
                else:
                    color = "#e8f5e9"
                font = "#1b5e20"

            flags = []
            if node.truncated_remainder and len(node.truncated_remainder) > 0:
                flags.append("TRUNCATED")
            if node.is_expanded:
                flags.append("EXPANDED")
            if flags:
                label = f"{label}\\n[{', '.join(flags)}]"

            lines.append(f'  {node_id} [label="{label}", shape=ellipse, '
                         f'fillcolor="{color}", fontcolor="{font}"];')
        else:
            cond = node.condition
            wp_short = cond.input_constraint[:40] + "..." if len(cond.input_constraint) > 40 else cond.input_constraint
            label = (f"({cond.loop_count}) {cond.condition_string}\\n"
                     f"WP: {wp_short}")
            lines.append(f'  {node_id} [label="{label}", '
                         f'fillcolor="#f5f5f5", fontcolor="#333333"];')

        if parent_id is not None:
            if is_right:
                lines.append(f'  {parent_id} -> {node_id} [label="T", color="#2e7d32"];')
            else:
                lines.append(f'  {parent_id} -> {node_id} [label="F", color="#c62828", style=dashed];')

        if node is not None and not node.is_leaf:
            self._dot_recursive(node.left, lines, is_right=False, parent_id=node_id)
            self._dot_recursive(node.right, lines, is_right=True, parent_id=node_id)

    def _dot_fault_recursive(self, node: Optional[Node], lines: List[str],
                             is_right: Optional[bool], parent_id: Optional[str]):
        node_id = f"node{self._counter}"
        self._counter += 1

        if node is None:
            label = "{Ø}"
            lines.append(f'  {node_id} [label="{label}", shape=ellipse, '
                         f'fillcolor="#eeeeee", fontcolor="#999999", style=dashed];')
        elif node.is_leaf:
            if node.test_cases == {INFEASIBLE_MARKER}:
                label = "INFEASIBLE"
                color = "#d6d6d6"
                font = "#555555"
            elif node.test_cases == {RANGE_EXCLUDED_MARKER}:
                label = "OUT-OF-RANGE"
                color = "#fff9c4"
                font = "#f57f17"
            else:
                sorted_cases = sorted(list(node.test_cases))
                failures = getattr(node, 'tbfv_failures', None) or {}
                failed_cases = [tc for tc in sorted_cases if tc in failures]
                if failed_cases:
                    label_parts = []
                    for tc in failed_cases[:5]:
                        fsf_ids = sorted({entry.get("fsf_id", "")
                                          for entry in failures.get(tc, [])})
                        fsf_label = ",".join(fsf_ids) if fsf_ids else "unknown"
                        if node.test_inputs and tc in node.test_inputs:
                            inp = node.test_inputs[tc]
                            inp_str = ", ".join(f"{k}={v}" for k, v in inp.items())
                            label_parts.append(f"{tc}({inp_str}) FAIL [{fsf_label}]")
                        else:
                            label_parts.append(f"{tc} FAIL [{fsf_label}]")
                    more = f" ... (+{len(failed_cases) - 5})" if len(failed_cases) > 5 else ""
                    label = "\\n".join(label_parts) + more
                    color = "#ffcdd2"
                    font = "#b71c1c"
                else:
                    label = ", ".join(sorted_cases[:5])
                    more = f" ... (+{len(sorted_cases) - 5})" if len(sorted_cases) > 5 else ""
                    label += more
                    color = "#e8f5e9"
                    font = "#1b5e20"

            lines.append(f'  {node_id} [label="{label}", shape=ellipse, '
                         f'fillcolor="{color}", fontcolor="{font}"];')
        else:
            cond = node.condition
            label = f"({cond.loop_count}) {cond.condition_string}"
            lines.append(f'  {node_id} [label="{label}", '
                         f'fillcolor="#f5f5f5", fontcolor="#333333"];')

        if parent_id is not None:
            if is_right:
                lines.append(f'  {parent_id} -> {node_id} [label="T", color="#2e7d32"];')
            else:
                lines.append(f'  {parent_id} -> {node_id} [label="F", color="#c62828", style=dashed];')

        if node is not None and not node.is_leaf:
            self._dot_fault_recursive(node.left, lines, is_right=False, parent_id=node_id)
            self._dot_fault_recursive(node.right, lines, is_right=True, parent_id=node_id)

    def _render_dot(self, dot_path: str, output_format: str,
                    output_path: Optional[str] = None,
                    timeout: int = 15,
                    label: str = "CCT",
                    png_dpi: int = 200) -> Optional[str]:
        """Render a DOT file through Graphviz and return the output path."""
        fmt = output_format.lower().lstrip(".")
        rendered_path = output_path or dot_path.replace('.dot', f'.{fmt}')
        cmd = ['dot', f'-T{fmt}', dot_path, '-o', rendered_path]
        if fmt == "png" and png_dpi:
            cmd.insert(2, f'-Gdpi={png_dpi}')
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
            print(f"{label} {fmt.upper()} rendered to: {rendered_path}")
            return rendered_path
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def save_dot(self, filepath: str):
        """Save DOT file."""
        dot_content = self.to_dot()
        with open(filepath, 'w') as f:
            f.write(dot_content)
        print(f"CCT DOT saved to: {filepath}")

    def render_dot(self, filepath: str, output_format: str,
                   output_path: Optional[str] = None,
                   label: str = "CCT") -> Optional[str]:
        """Render a saved DOT file to the requested format."""
        return self._render_dot(filepath, output_format, output_path=output_path, label=label)

    def save_svg(self, filepath: str) -> Optional[str]:
        """Render a saved CCT DOT file to SVG."""
        return self.render_dot(filepath, "svg", label="CCT")

    def save_pdf(self, filepath: str) -> Optional[str]:
        """Render a saved CCT DOT file to PDF."""
        return self.render_dot(filepath, "pdf", label="CCT")

    def save_tbfv_fault_dot(self, filepath: str):
        """Save the TBFV fault DOT view."""
        dot_content = self.to_tbfv_fault_dot()
        with open(filepath, 'w') as f:
            f.write(dot_content)
        print(f"TBFV fault DOT saved to: {filepath}")

    def save_tbfv_fault_svg(self, filepath: str) -> Optional[str]:
        """Render a saved TBFV fault DOT file to SVG."""
        return self.render_dot(filepath, "svg", label="TBFV fault")

    def save_tbfv_fault_pdf(self, filepath: str) -> Optional[str]:
        """Render a saved TBFV fault DOT file to PDF."""
        return self.render_dot(filepath, "pdf", label="TBFV fault")


MAX_LOOP_BOUND = CCT.MAX_LOOP_BOUND
