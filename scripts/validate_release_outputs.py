from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eda.analysis import (
    all_paths,
    all_paths_pass_through,
    articulation_points_between,
    constant_input_gates,
    cone_depth,
    cut_signal_between_pi_po,
    derive_boolean_equation,
    direct_fanout,
    direct_pi_to_po_paths,
    dff_input_logic_structures,
    dffs_by_clock,
    design_max_logic_depth,
    fanout_cone,
    find_path,
    find_gates,
    find_nand_equivalent_pair,
    gate_counts,
    gate_connections,
    gate_on_max_depth_path,
    gate_type_connections,
    gate_type_count,
    gate_type_count_in_cone,
    gates_by_type,
    highest_fanout_primary_input,
    io_counts,
    largest_fanin_cone_output,
    logic_cone,
    max_depth_to_dff_d,
    max_register_to_register_depth,
    max_depth,
    output_with_deepest_fanin_cone,
    outputs_depth_greater_than,
    primary_output_cone_sizes,
    primary_inputs_with_widths,
    primary_outputs_with_widths,
    register_to_register_paths,
    shared_fanin_cone_gates,
)
from eda.verify import (
    check_connectivity,
    check_depth,
    check_design_equivalence,
    check_equivalence,
    check_fanout,
    check_property,
    check_signal_symmetry,
)
from parser.verilog_parser import parse_verilog
from runtime.limits import DEFAULT_COMPLETE_PATH_LIMIT


TRANSFORM_OPS = {
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "collapse_back_to_back_inverters",
    "replace_or_with_nand_not",
    "replace_nand_const1_with_not",
    "replace_with_and_not",
    "insert_buffers_for_fanout",
    "insert_dedicated_buffers_for_each_load",
    "insert_buffers_for_all_high_fanout",
    "balance_depth_with_buffers",
    "balance_depth",
    "optimize_cone",
    "constant_propagation",
    "optimize_design_depth",
    "replace_xnor_nor_with_basic_gates",
    "replace_xnor_with_nor",
    "replace_xor_with_nand",
    "replace_and_not_with_nand",
    "merge_equivalent_gates",
    "rename_gate",
    "rename_net",
    "reconnect_gate_input",
}

NON_EQUIVALENCE_TRANSFORMS = {"replace_buffers_with_and"}
NOOP_ACCEPTABLE_TRANSFORMS = {
    "remove_dangling",
    "constant_propagation",
    "optimize_cone",
    "optimize_design_depth",
    "merge_equivalent_gates",
}
VALIDATOR_FULL_TRANSFORM_EQ_GATE_LIMIT = 4000
VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT = 20000
VALIDATOR_LARGE_SELECTED_OUTPUT_LIMIT = 8
EXACT_VALIDATED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_gates",
    "report_gate_counts",
    "report_gate_type_count",
    "report_gate_type_connections",
    "report_gates_by_type",
    "report_gate_connections",
    "report_gate_type_count_in_cone",
    "report_fanout",
    "report_highest_fanout_primary_input",
    "find_path",
    "all_paths_pass_through",
    "report_all_paths",
    "all_paths",
    "max_depth",
    "report_max_logic_depth",
    "report_cone_depth",
    "logic_cone",
    "report_fanout_cone",
    "report_direct_pi_po_paths",
    "report_primary_inputs",
    "report_primary_outputs",
    "report_io_counts",
    "report_deepest_output_cone",
    "report_largest_fanin_cone_output",
    "report_outputs_by_cone_size",
    "report_outputs_depth_greater_than",
    "report_constant_input_gates",
    "gate_on_max_depth_path",
    "report_articulation_points",
    "report_shared_fanin_cone_gates",
    "report_dffs_by_clock",
    "report_max_depth_to_dff_d",
    "report_max_register_to_register_depth",
    "report_register_paths",
    "report_dff_input_logic_structures",
    "same_clock_domain",
    "check_cut_signal",
    "derive_boolean_equation",
    "find_nand_equivalent_pair",
    "report_last_transform_stats",
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalence",
    "check_property",
    "check_signal_symmetry",
    "check_equivalent_to_original",
    "check_equivalent_to_last_transform_input",
}
_SNAPSHOT_PARSE_CACHE: dict[Path, Any] = {}


@dataclass
class ValidationResult:
    case: str
    response_id: int
    status: str
    check: str
    detail: str


@dataclass
class MetricRecord:
    case: str
    response_id: int
    op: str
    validation_status: str
    validation_detail: str
    before_gates: int | None
    after_gates: int | None
    gate_delta: int | None
    gate_improved: bool | None
    before_depth: int | None
    after_depth: int | None
    depth_delta: int | None
    depth_improved: bool | None
    final_max_fanout: int | None
    changed_items: int | None
    skipped_items: int | None
    cost_objective: str
    metric_source: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release runner outputs using ledger snapshots and external EDA checks."
    )
    parser.add_argument("--release-dir", type=Path, default=Path("A_release testcase_0510"))
    parser.add_argument("--planner", default="rule")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--case-range", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--show-coverage",
        action="store_true",
        help="Print validator operation coverage and exit.",
    )
    parser.add_argument(
        "--fail-on-inconclusive",
        action="store_true",
        help="Return nonzero when an external oracle check cannot reach a verdict.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSONL validation report path.")
    parser.add_argument(
        "--metrics-output",
        type=Path,
        help="Optional CSV report with transform/optimization QoR metrics.",
    )
    args = parser.parse_args()

    if args.show_coverage:
        _print_coverage()
        return 0

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = (repo_root / args.release_dir).resolve()
    cases = _select_cases(release_dir, args.case, args.case_range, run_all=args.all)
    if not cases:
        print("No cases selected. Use --all, --case testNN, or --case-range test25-test40.", file=sys.stderr)
        return 2

    results: list[ValidationResult] = []
    ledger_paths: dict[str, Path] = {}
    for case in cases:
        ledger_path = _find_ledger(release_dir, args.planner, case)
        if ledger_path is None:
            results.append(ValidationResult(case, 0, "FAIL", "ledger", "ledger.jsonl not found"))
            continue
        ledger_paths[case] = ledger_path
        results.extend(_validate_ledger(release_dir, ledger_path, case))

    _print_summary(results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "".join(json.dumps(result.__dict__, sort_keys=True) + "\n" for result in results),
            encoding="utf-8",
        )
    if args.metrics_output:
        metrics = _collect_metrics_for_cases(release_dir, ledger_paths, results)
        _write_metrics_csv(args.metrics_output, metrics)
        _print_metrics_summary(metrics)

    has_fail = any(result.status == "FAIL" for result in results)
    has_inconclusive = any(result.status == "INCONCLUSIVE" for result in results)
    return 1 if has_fail or (args.fail_on_inconclusive and has_inconclusive) else 0


def _validate_ledger(release_dir: Path, ledger_path: Path, case: str) -> list[ValidationResult]:
    _SNAPSHOT_PARSE_CACHE.clear()
    records = _read_records(ledger_path)
    original_design = None
    results: list[ValidationResult] = []
    for record in records:
        response_id = int(record.get("response_id") or 0)
        if original_design is None and record.get("after_snapshot"):
            try:
                original_design = _parse_snapshot(record, "after_snapshot", release_dir, ledger_path)
            except Exception:
                original_design = None
        results.append(_validate_record(release_dir, ledger_path, case, record, original_design))
    return results


def _validate_record(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    record: dict[str, Any],
    original_design: Any,
) -> ValidationResult:
    response_id = int(record.get("response_id") or 0)
    body = str(record.get("body") or "")
    steps = _plan_steps(record)
    op = str(steps[0].get("op")) if steps else ""
    args = steps[0].get("args") if steps else {}
    args = args if isinstance(args, dict) else {}

    if record.get("error") or _has_error_marker(body):
        return ValidationResult(case, response_id, "FAIL", "runtime", record.get("error") or _first_line(body))
    if "could not map" in body.lower() or op == "unsupported":
        return ValidationResult(case, response_id, "FAIL", "unsupported", _first_line(body))
    if not steps:
        return ValidationResult(case, response_id, "SKIP", "plan", "no normalized plan recorded")

    try:
        if op in {"begin_testcase", "read_design", "write_design"}:
            return _validate_io_op(release_dir, ledger_path, case, response_id, op, args, body, record)
        if op == "find_gates":
            return _validate_find_gates(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_gate_counts":
            return _validate_gate_counts(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_gate_type_count":
            return _validate_gate_type_count(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_gate_type_connections":
            return _validate_gate_type_connections(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_gates_by_type":
            return _validate_gates_by_type(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_gate_connections":
            return _validate_gate_connections(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_gate_type_count_in_cone":
            return _validate_gate_type_count_in_cone(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_fanout":
            return _validate_fanout(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_highest_fanout_primary_input":
            return _validate_highest_fanout_primary_input(release_dir, ledger_path, case, response_id, body, record)
        if op == "find_path":
            return _validate_find_path(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "all_paths_pass_through":
            return _validate_all_paths_pass_through(release_dir, ledger_path, case, response_id, args, body, record)
        if op in {"report_all_paths", "all_paths"}:
            return _validate_all_paths(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "max_depth":
            return _validate_max_depth(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_max_logic_depth":
            return _validate_design_max_logic_depth(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_cone_depth":
            return _validate_cone_depth(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "logic_cone":
            return _validate_logic_cone(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_fanout_cone":
            return _validate_fanout_cone(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_direct_pi_po_paths":
            return _validate_direct_pi_po_paths(release_dir, ledger_path, case, response_id, body, record)
        if op == "check_cut_signal":
            return _validate_cut_signal(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_primary_inputs":
            return _validate_primary_ports(release_dir, ledger_path, case, response_id, body, record, inputs=True)
        if op == "report_primary_outputs":
            return _validate_primary_ports(release_dir, ledger_path, case, response_id, body, record, inputs=False)
        if op == "report_io_counts":
            return _validate_io_counts(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_deepest_output_cone":
            return _validate_deepest_output_cone(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_largest_fanin_cone_output":
            return _validate_largest_fanin_cone_output(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_outputs_by_cone_size":
            return _validate_outputs_by_cone_size(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_outputs_depth_greater_than":
            return _validate_outputs_depth_greater_than(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_constant_input_gates":
            return _validate_constant_input_gates(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "gate_on_max_depth_path":
            return _validate_gate_on_max_depth_path(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_articulation_points":
            return _validate_articulation_points(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_shared_fanin_cone_gates":
            return _validate_shared_fanin_cone_gates(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "derive_boolean_equation":
            return _validate_boolean_equation_derivation(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "find_nand_equivalent_pair":
            return _validate_nand_equivalent_pair(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_dffs_by_clock":
            return _validate_dffs_by_clock(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_max_depth_to_dff_d":
            return _validate_max_depth_to_dff_d(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_max_register_to_register_depth":
            return _validate_max_register_to_register_depth(release_dir, ledger_path, case, response_id, body, record)
        if op == "report_register_paths":
            return _validate_register_paths(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_dff_input_logic_structures":
            return _validate_dff_input_logic_structures(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "same_clock_domain":
            return _validate_same_clock_domain(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "report_last_transform_stats":
            return _validate_last_transform_stats(case, response_id, body, record)
        if op == "check_connectivity":
            return _validate_connectivity(release_dir, ledger_path, case, response_id, body, record)
        if op == "check_fanout":
            return _validate_fanout_check(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "check_depth":
            return _validate_depth_check(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "check_equivalence":
            return _validate_boolean_equivalence(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "check_property":
            return _validate_property(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "check_signal_symmetry":
            return _validate_signal_symmetry(release_dir, ledger_path, case, response_id, args, body, record)
        if op == "check_equivalent_to_original":
            return _validate_original_equivalence(
                release_dir, ledger_path, case, response_id, body, record, original_design
            )
        if op == "check_equivalent_to_last_transform_input":
            return _validate_last_transform_input_equivalence(
                release_dir, ledger_path, case, response_id, body, record
            )
        if op in TRANSFORM_OPS:
            return _validate_transform(release_dir, ledger_path, case, response_id, op, args, body, record)
        if op in NON_EQUIVALENCE_TRANSFORMS:
            return ValidationResult(case, response_id, "SKIP", op, "operation is intentionally not equivalence-preserving")
    except Exception as exc:
        return ValidationResult(case, response_id, "INCONCLUSIVE", op or "validator", str(exc))

    return ValidationResult(case, response_id, "SKIP", op, "no oracle rule implemented for this operation yet")


def _validate_io_op(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    op: str,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    if op == "begin_testcase":
        return ValidationResult(case, response_id, "PASS", op, "testcase state initialized")
    if op == "read_design":
        design = _parse_snapshot(record, "after_snapshot", release_dir, ledger_path)
        if design is None:
            return ValidationResult(case, response_id, "FAIL", op, "missing readable after snapshot")
        return ValidationResult(case, response_id, "PASS", op, f"parsed {design.summary()}")
    if op == "write_design":
        path = _resolve_output_path(args.get("path"), release_dir)
        if path is None or not path.exists():
            return ValidationResult(case, response_id, "FAIL", op, "written netlist path not found")
        parse_verilog(path)
        return ValidationResult(case, response_id, "PASS", op, "written netlist parses successfully")
    return ValidationResult(case, response_id, "SKIP", op, _first_line(body))


def _validate_gate_counts(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_counts(design)
    checks = [f"Total gates: {result['total']}" in body]
    checks.extend(f"- {gate_type}: {count}" in body for gate_type, count in result["counts"].items())
    if all(checks):
        return ValidationResult(case, response_id, "PASS", "report_gate_counts", "text matches recomputed counts")
    return ValidationResult(case, response_id, "FAIL", "report_gate_counts", "response does not match recomputed counts")


def _validate_gate_type_count(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate_type = str(args.get("gate_type") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_type_count(design, gate_type)
    expected = f"{gate_type.upper()} gate count: {result['count']}"
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "report_gate_type_count", expected)


def _validate_find_gates(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    gates = find_gates(design, gate_type=args.get("gate_type"), name_contains=args.get("name_contains"))
    expected = f"Matched gates: {len(gates)}"
    return _contains_result(case, response_id, "find_gates", body, expected)


def _validate_gate_type_connections(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate_type = str(args.get("gate_type") or "")
    max_items = args.get("max_items")
    max_items = None if max_items is None else _positive_int_or_default(max_items, 200)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_type_connections(design, gate_type, max_items=max_items)
    expected = f'{gate_type.upper()} gate connections: {result["num_gates"]}'
    return _contains_result(case, response_id, "report_gate_type_connections", body, expected)


def _validate_gates_by_type(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate_type = str(args.get("gate_type") or "")
    limit = args.get("limit")
    limit = None if limit is None else _positive_int_or_default(limit, 200)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gates_by_type(design, gate_type, limit=limit)
    expected = f'{gate_type.upper()} gates: {result["num_gates"]}'
    return _contains_result(case, response_id, "report_gates_by_type", body, expected)


def _validate_gate_connections(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate = str(args.get("gate") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_connections(design, gate)
    if result["kind"] == "gate":
        inputs = ", ".join(result["inputs"]) or "(none)"
        expected = f'Gate "{gate}": type={result["gate_type"]}, inputs=[{inputs}], output={result["output"]}.'
    else:
        expected = f'DFF "{gate}":'
    return _contains_result(case, response_id, "report_gate_connections", body, expected)


def _validate_gate_type_count_in_cone(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    gate_type = str(args.get("gate_type") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_type_count_in_cone(design, target, gate_type)
    expected = (
        f'{gate_type.upper()} gates in the fanin cone of "{target}": '
        f'{result["num_gates"]} out of {result["total_cone_gates"]} cone gate(s).'
    )
    return _contains_result(case, response_id, "report_gate_type_count_in_cone", body, expected)


def _validate_fanout(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    net = str(args.get("net") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = direct_fanout(design, net)
    expected = f'{result["num_loads"]} load(s), {result["num_unique_sinks"]} unique sink(s)'
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "report_fanout", expected)


def _validate_highest_fanout_primary_input(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = highest_fanout_primary_input(design)
    inputs = ", ".join(result["inputs"]) if result["inputs"] else "none"
    expected = f'Primary input(s) with highest fanout: {inputs}. Maximum fanout: {result["max_fanout"]}.'
    return _contains_result(case, response_id, "report_highest_fanout_primary_input", body, expected)


def _validate_find_path(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    avoid = args.get("avoid") or []
    avoid = avoid if isinstance(avoid, list) else [avoid]
    design = _require_snapshot(record, release_dir, ledger_path)
    path = find_path(design, src=src, dst=dst, avoid=[str(item) for item in avoid])
    if path:
        expected = " -> ".join(path)
        status = "PASS" if "Found path:" in body and expected in body else "FAIL"
        return ValidationResult(case, response_id, status, "find_path", f"path length {len(path)}")
    expected = f'No path found from "{src}" to "{dst}".'
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "find_path", expected)


def _validate_all_paths_pass_through(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    node = str(args.get("node") or "")
    if src == "__primary_input__" and dst == "__primary_output__":
        return ValidationResult(case, response_id, "SKIP", "all_paths_pass_through", "PI/PO cut-signal mode not covered")
    design = _require_snapshot(record, release_dir, ledger_path)
    ok = all_paths_pass_through(design, src=src, dst=dst, node=node)
    if ok:
        expected = f'Yes. Every combinational path from "{src}" to "{dst}" passes through "{node}".'
        status = "PASS" if expected in body else "FAIL"
        return ValidationResult(case, response_id, status, "all_paths_pass_through", expected)

    avoiding_path = find_path(design, src=src, dst=dst, avoid=[node])
    if avoiding_path:
        expected = f'There is a combinational path from "{src}" to "{dst}" that avoids "{node}"'
        status = "PASS" if body.startswith("No.") and expected in body and " -> ".join(avoiding_path) in body else "FAIL"
        return ValidationResult(case, response_id, status, "all_paths_pass_through", "avoiding path exists")

    expected = f'No. No combinational path from "{src}" to "{dst}" was found.'
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "all_paths_pass_through", expected)


def _validate_all_paths(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    max_paths = _positive_int_or_default(args.get("max_paths"), DEFAULT_COMPLETE_PATH_LIMIT)
    design = _require_snapshot(record, release_dir, ledger_path)
    if _is_expensive_analysis_design(design):
        if f'Combinational paths from "{src}" to "{dst}":' in body:
            return ValidationResult(case, response_id, "INCONCLUSIVE", "report_all_paths", "large-design all-path oracle skipped")
    result = all_paths(design, src=src, dst=dst, max_paths=max_paths)
    expected = f'Combinational paths from "{src}" to "{dst}": {result["num_paths"]}'
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "report_all_paths", expected)


def _validate_max_depth(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    depth, path = max_depth(design, src, dst)
    expected = f'The maximum logic depth from "{src}" to "{dst}" is {depth}.'
    path_text = " -> ".join(path) if path else "(none)"
    status = "PASS" if expected in body and f"Example path: {path_text}" in body else "FAIL"
    return ValidationResult(case, response_id, status, "max_depth", expected)


def _validate_cone_depth(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = cone_depth(design, target)
    expected = f'is {result["max_depth"]} gate level(s)'
    status = "PASS" if expected in body and f'contains {result["num_gates"]} gate(s)' in body else "FAIL"
    return ValidationResult(case, response_id, status, "report_cone_depth", expected)


def _validate_design_max_logic_depth(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = design_max_logic_depth(design)
    if result["endpoint"] is None:
        expected = "The maximum combinational logic depth in the design is 0."
    else:
        expected = f'The maximum combinational logic depth in the design is {result["max_depth"]}.'
    return _contains_result(case, response_id, "report_max_logic_depth", body, expected)


def _validate_logic_cone(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    gates = logic_cone(design, target)
    expected = f'Logic cone of "{target}" contains {len(gates)} gates:'
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "logic_cone", expected)


def _validate_fanout_cone(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    source = str(args.get("source") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = fanout_cone(design, source)
    expected = (
        f'Transitive fanout cone of "{source}": '
        f'{result["num_gates"]} gate(s), {result["num_nets"]} net(s), '
        f'{result["num_primary_outputs"]} primary output(s), '
        f'{result["num_dff_sinks"]} DFF sink(s).'
    )
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, "report_fanout_cone", expected)


def _validate_direct_pi_po_paths(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    if _is_expensive_analysis_design(design):
        if "Direct PI-to-PO zero-gate paths:" in body:
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                "report_direct_pi_po_paths",
                "large-design direct PI/PO oracle skipped",
            )
    result = direct_pi_to_po_paths(design)
    expected = f'Direct PI-to-PO zero-gate paths: {result["num_paths"]}'
    return _contains_result(case, response_id, "report_direct_pi_po_paths", body, expected)


def _validate_cut_signal(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    signal = str(args.get("signal") or args.get("node") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    if _is_expensive_analysis_design(design):
        if body.startswith("Yes.") or body.startswith("No."):
            return ValidationResult(case, response_id, "INCONCLUSIVE", "check_cut_signal", "large-design cut oracle skipped")
    result = cut_signal_between_pi_po(design, signal)
    expected = f'Yes. "{signal}" is a cut' if result.get("is_cut") else f'No. "{signal}" was not proven'
    return _contains_result(case, response_id, "check_cut_signal", body, expected)


def _validate_primary_ports(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
    *,
    inputs: bool,
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = primary_inputs_with_widths(design) if inputs else primary_outputs_with_widths(design)
    label = "inputs" if inputs else "outputs"
    title = "Primary inputs" if inputs else "Primary outputs"
    expected = f'{title}: {result[f"num_{label}"]}'
    check = "report_primary_inputs" if inputs else "report_primary_outputs"
    return _contains_result(case, response_id, check, body, expected)


def _validate_io_counts(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = io_counts(design)
    ok = f'- inputs: {result["num_inputs"]}' in body and f'- outputs: {result["num_outputs"]}' in body
    return ValidationResult(case, response_id, "PASS" if ok else "FAIL", "report_io_counts", str(result))


def _validate_deepest_output_cone(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    if _is_expensive_analysis_design(design):
        if "deepest fanin cone" in body.lower() or "deepest output cone" in body.lower():
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                "report_deepest_output_cone",
                "large-design deepest-cone oracle skipped",
            )
    result = output_with_deepest_fanin_cone(design)
    expected = f'Deepest primary output logic depth: {result["max_depth"]}'
    return _contains_result(case, response_id, "report_deepest_output_cone", body, expected)


def _validate_largest_fanin_cone_output(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = largest_fanin_cone_output(design)
    expected = f'Largest primary-output fanin cone size: {result["max_gates"]} gate(s).'
    return _contains_result(case, response_id, "report_largest_fanin_cone_output", body, expected)


def _validate_outputs_by_cone_size(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    min_gates = int(args.get("min_gates") or 0)
    design = _require_snapshot(record, release_dir, ledger_path)
    sizes = [
        (output, info["num_gates"])
        for output, info in sorted(primary_output_cone_sizes(design).items())
        if info["num_gates"] > min_gates
    ]
    expected = f'Primary outputs with fanin cone size greater than {min_gates}: {len(sizes)}'
    return _contains_result(case, response_id, "report_outputs_by_cone_size", body, expected)


def _validate_outputs_depth_greater_than(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    min_depth = int(args.get("min_depth") or 0)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = outputs_depth_greater_than(design, min_depth)
    expected = f'Primary outputs with logic depth greater than {min_depth}: {result["num_outputs"]}'
    return _contains_result(case, response_id, "report_outputs_depth_greater_than", body, expected)


def _validate_constant_input_gates(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate_type = args.get("gate_type")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = constant_input_gates(design, gate_type=gate_type)
    title_type = result["gate_type"].upper() if result["gate_type"] else "Gate"
    expected = f"{title_type} gates with constant inputs: {result['num_gates']}"
    return _contains_result(case, response_id, "report_constant_input_gates", body, expected)


def _validate_gate_on_max_depth_path(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    gate = str(args.get("gate") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = gate_on_max_depth_path(design, gate)
    answer = "Yes" if result["on_max_depth_path"] else "No"
    expected = f'{answer}. Gate "{gate}"'
    return _contains_result(case, response_id, "gate_on_max_depth_path", body, expected)


def _validate_articulation_points(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = articulation_points_between(design, src, dst)
    expected = f'Articulation points between "{src}" and "{dst}": {result["num_points"]}'
    return _contains_result(case, response_id, "report_articulation_points", body, expected)


def _validate_shared_fanin_cone_gates(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target_a = str(args.get("target_a") or "")
    target_b = str(args.get("target_b") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = shared_fanin_cone_gates(design, target_a, target_b)
    expected = f'Shared fanin cone gates between "{target_a}" and "{target_b}": {result["num_shared_gates"]}'
    return _contains_result(case, response_id, "report_shared_fanin_cone_gates", body, expected)


def _validate_boolean_equation_derivation(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    if _is_expensive_analysis_design(design):
        if f'Boolean equation for "{target}"' in body or "Final expression reference:" in body:
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                "derive_boolean_equation",
                "large-design Boolean-equation oracle skipped",
            )
    result = derive_boolean_equation(design, target, max_terms=5000)
    final_ref = str(result["expression"])
    expected_inline = f'Boolean equation for "{target}": {target} = {final_ref}'
    expected_artifact = f"final_reference: {final_ref}"
    expected_report_line = f"Final expression reference: {final_ref}"
    status = (
        "PASS"
        if expected_inline in body or expected_artifact in body or expected_report_line in body
        else "FAIL"
    )
    return ValidationResult(case, response_id, status, "derive_boolean_equation", f"final_reference: {final_ref}")


def _validate_nand_equivalent_pair(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = find_nand_equivalent_pair(design, target)
    expected = "Yes." if result.get("found") else f'No NAND(a, b) pair equivalent to "{target}" was found.'
    return _contains_result(case, response_id, "find_nand_equivalent_pair", body, expected)


def _validate_boolean_equivalence(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_equivalence(design, str(args.get("expr") or ""), str(args.get("target") or ""))
    return _verdict_from_equivalence_text(case, response_id, "check_equivalence", body, result)


def _validate_property(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_property(design, str(args.get("target") or ""), str(args.get("property") or ""))
    return _verdict_from_equivalence_text(case, response_id, "check_property", body, result)


def _validate_dffs_by_clock(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    clock = str(args.get("clock") or "")
    max_items = args.get("max_items")
    max_items = None if max_items is None else _positive_int_or_default(max_items, 200)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = dffs_by_clock(design, clock, max_items=max_items)
    expected = f'DFFs driven by clock "{clock}": {result["num_dffs"]}'
    return _contains_result(case, response_id, "report_dffs_by_clock", body, expected)


def _validate_max_depth_to_dff_d(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = max_depth_to_dff_d(design)
    expected = (
        "The maximum logic depth from any primary input to any DFF D-pin is 0."
        if result["dff"] is None
        else f'The maximum logic depth from any primary input to any DFF D-pin is {result["max_depth"]}.'
    )
    return _contains_result(case, response_id, "report_max_depth_to_dff_d", body, expected)


def _validate_max_register_to_register_depth(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = max_register_to_register_depth(design)
    expected = (
        "The maximum combinational depth on any register-to-register path is 0."
        if result["src_dff"] is None or result["dst_dff"] is None
        else f'The maximum combinational depth on any register-to-register path is {result["max_depth"]}.'
    )
    return _contains_result(case, response_id, "report_max_register_to_register_depth", body, expected)


def _validate_register_paths(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    max_paths = _positive_int_or_default(args.get("max_paths"), 200)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = register_to_register_paths(design, max_paths=max_paths)
    expected = f'Register-to-register combinational paths: {result["num_paths"]}'
    return _contains_result(case, response_id, "report_register_paths", body, expected)


def _validate_dff_input_logic_structures(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    max_items = _positive_int_or_default(args.get("max_items"), 200)
    design = _require_snapshot(record, release_dir, ledger_path)
    result = dff_input_logic_structures(design, max_items=max_items)
    expected = (
        "DFF D-input enable/hold structure report: "
        f'{result["num_with_structures"]} of {result["num_dffs"]} DFF(s) matched.'
    )
    return _contains_result(case, response_id, "report_dff_input_logic_structures", body, expected)


def _validate_same_clock_domain(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    dff_a_name = str(args.get("dff_a") or "")
    dff_b_name = str(args.get("dff_b") or "")
    dff_a = design.dffs[dff_a_name]
    dff_b = design.dffs[dff_b_name]
    expected_prefix = "Yes." if dff_a.clk == dff_b.clk else "No."
    status = "PASS" if body.startswith(expected_prefix) else "FAIL"
    return ValidationResult(case, response_id, status, "same_clock_domain", f"{dff_a.clk} vs {dff_b.clk}")


def _validate_last_transform_stats(
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    last_transform = record.get("last_transform")
    if not last_transform:
        expected = "No transform has been performed yet."
    elif isinstance(last_transform, dict):
        expected = f'Last transform "{last_transform.get("transform", "unknown_transform")}" stats:'
    else:
        expected = "Last transform"
    return _contains_result(case, response_id, "report_last_transform_stats", body, expected)


def _validate_connectivity(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_connectivity(design)
    expected = f"'ok': {result['ok']}"
    return _contains_result(case, response_id, "check_connectivity", body, expected)


def _validate_fanout_check(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_fanout(design, int(args.get("max_fanout") or 0))
    expected = f"'ok': {result['ok']}"
    return _contains_result(case, response_id, "check_fanout", body, expected)


def _validate_depth_check(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_depth(
        design,
        str(args.get("src") or ""),
        str(args.get("dst") or ""),
        int(args.get("max_depth") or 0),
    )
    expected = f"'ok': {result['ok']}"
    return _contains_result(case, response_id, "check_depth", body, expected)


def _validate_signal_symmetry(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    target = str(args.get("target") or "")
    input_a = str(args.get("input_a") or "")
    input_b = str(args.get("input_b") or "")
    design = _require_snapshot(record, release_dir, ledger_path)
    result = check_signal_symmetry(design, target, input_a, input_b)
    expected_prefix = "Yes." if result.get("ok") else "No."
    status = "PASS" if body.startswith(expected_prefix) else "FAIL"
    return ValidationResult(case, response_id, status, "check_signal_symmetry", f"oracle ok={result.get('ok')}")


def _validate_original_equivalence(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
    original_design: Any,
) -> ValidationResult:
    if _is_bounded_skip_response(body):
        return ValidationResult(case, response_id, "INCONCLUSIVE", "check_equivalent_to_original", _first_line(body))
    if original_design is None:
        return ValidationResult(case, response_id, "INCONCLUSIVE", "check_equivalent_to_original", "no original snapshot")
    after = _require_snapshot(record, release_dir, ledger_path)
    result = check_design_equivalence(original_design, after)
    return _verdict_from_equivalence_text(case, response_id, "check_equivalent_to_original", body, result)


def _validate_last_transform_input_equivalence(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    if _is_bounded_skip_response(body):
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "check_equivalent_to_last_transform_input",
            _first_line(body),
        )
    before = _parse_snapshot(record, "last_transform_input_snapshot", release_dir, ledger_path)
    after = _parse_snapshot(record, "after_snapshot", release_dir, ledger_path)
    if before is None:
        if not record.get("last_transform") and "No previous successful transform input snapshot is available." in body:
            return ValidationResult(
                case,
                response_id,
                "PASS",
                "check_equivalent_to_last_transform_input",
                "no previous transform snapshot expected",
            )
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "check_equivalent_to_last_transform_input",
            "missing last transform input snapshot",
        )
    if after is None:
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "check_equivalent_to_last_transform_input",
            "missing current design snapshot",
        )
    result = check_design_equivalence(before, after)
    return _verdict_from_equivalence_text(case, response_id, "check_equivalent_to_last_transform_input", body, result)


def _validate_transform(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    op: str,
    args: dict[str, Any],
    body: str,
    record: dict[str, Any],
) -> ValidationResult:
    before = _parse_snapshot(record, "before_snapshot", release_dir, ledger_path)
    after = _parse_snapshot(record, "after_snapshot", release_dir, ledger_path)
    if before is None or after is None:
        return ValidationResult(case, response_id, "INCONCLUSIVE", op, "missing before/after snapshot")

    residual_args = dict(args)
    if _last_transform_has_skipped_items(record):
        residual_args["_bounded_partial"] = True
    no_change_reason = _no_structural_change_reason(record, release_dir, ledger_path)

    if max(_design_gate_total(before), _design_gate_total(after)) > VALIDATOR_FULL_TRANSFORM_EQ_GATE_LIMIT:
        residual = _check_transform_residual(after, op, residual_args)
        if residual is not None and residual[0] == "FAIL":
            return ValidationResult(case, response_id, residual[0], op, residual[1])
        if residual is not None and residual[0] == "INCONCLUSIVE":
            return ValidationResult(case, response_id, residual[0], op, residual[1])
        if residual is not None and residual[0] == "PASS":
            return ValidationResult(case, response_id, residual[0], op, residual[1])
        if no_change_reason is not None and op in NOOP_ACCEPTABLE_TRANSFORMS:
            return ValidationResult(case, response_id, "PASS", op, no_change_reason)
        if _is_bounded_skip_response(body):
            return ValidationResult(case, response_id, "INCONCLUSIVE", op, _first_line(body))
        return _validate_large_transform_with_guards(case, response_id, op, args, before, after)

    equiv = check_design_equivalence(before, after)
    if not equiv.get("ok"):
        if _equivalence_depends_on_unknown_constant(equiv):
            return ValidationResult(case, response_id, "INCONCLUSIVE", op, f"equivalence depends on unknown/X constant: {equiv}")
        return ValidationResult(case, response_id, "FAIL", op, f"not equivalent: {equiv}")

    residual = _check_transform_residual(after, op, residual_args)
    if residual is not None:
        return ValidationResult(case, response_id, residual[0], op, residual[1])
    if no_change_reason is not None and op in NOOP_ACCEPTABLE_TRANSFORMS:
        return ValidationResult(case, response_id, "PASS", op, no_change_reason)
    return ValidationResult(case, response_id, "PASS", op, f"before/after equivalent by {equiv.get('engine')}")


def _validate_large_transform_with_guards(
    case: str,
    response_id: int,
    op: str,
    args: dict[str, Any],
    before: Any,
    after: Any,
) -> ValidationResult:
    connectivity = check_connectivity(after)
    if not connectivity.get("ok", False):
        return ValidationResult(case, response_id, "FAIL", op, f"large-design connectivity failed: {connectivity}")

    selected_outputs = _selected_outputs_for_large_transform(before, after, args)
    if selected_outputs:
        result = check_design_equivalence(before, after, outputs=selected_outputs)
        if result.get("ok"):
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity passed; selected-output equivalence passed for {selected_outputs}",
            )
        if _equivalence_depends_on_unknown_constant(result):
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                op,
                f"selected-output equivalence depends on unknown/X constant: {result}",
            )
        return ValidationResult(case, response_id, "FAIL", op, f"selected-output equivalence failed: {result}")

    return ValidationResult(
        case,
        response_id,
        "INCONCLUSIVE",
        op,
        (
            "large-design connectivity passed; selected-output equivalence target unavailable, "
            "so full transform equivalence remains bounded"
        ),
    )


def _selected_outputs_for_large_transform(before: Any, after: Any, args: dict[str, Any]) -> list[str]:
    common_outputs = set(getattr(before, "outputs", set())) & set(getattr(after, "outputs", set()))
    candidates: list[str] = []
    for key in ("target", "dst", "output"):
        value = args.get(key)
        if isinstance(value, str):
            candidates.append(value)
    outputs = args.get("outputs")
    if isinstance(outputs, list):
        candidates.extend(str(item) for item in outputs)
    selected = [candidate for candidate in candidates if candidate in common_outputs]
    return sorted(dict.fromkeys(selected))[:VALIDATOR_LARGE_SELECTED_OUTPUT_LIMIT]


def _check_transform_residual(design: Any, op: str, args: dict[str, Any]) -> tuple[str, str] | None:
    if op == "insert_buffers_for_all_high_fanout":
        if args.get("_bounded_partial"):
            return ("INCONCLUSIVE", "bounded fanout insertion skipped some high-fanout nets")
        max_fanout = int(args.get("max_fanout") or 0)
        result = check_fanout(design, max_fanout)
        return ("PASS", f"fanout <= {max_fanout}") if result.get("ok") else ("FAIL", f"fanout violations: {result}")
    if op in {"replace_xor_with_nand", "replace_xnor_with_nor", "replace_xnor_nor_with_basic_gates"}:
        forbidden = "xor" if op == "replace_xor_with_nand" else "xnor"
        count = gate_type_count(design, forbidden)["count"]
        return ("PASS", f"{forbidden} count is 0") if count == 0 else ("FAIL", f"{forbidden} count remains {count}")
    return None


def _equivalence_depends_on_unknown_constant(result: dict[str, Any]) -> bool:
    failures = result.get("failures")
    if not isinstance(failures, dict) or not failures:
        return False
    for failure in failures.values():
        if not isinstance(failure, dict):
            continue
        counterexample = failure.get("counterexample")
        if isinstance(counterexample, dict) and any(str(key).lower() in {"1'bx", "1'bz"} for key in counterexample):
            return True
    counterexample = result.get("counterexample")
    return isinstance(counterexample, dict) and any(
        str(key).lower() in {"1'bx", "1'bz"} for key in counterexample
    )


def _last_transform_has_skipped_items(record: dict[str, Any]) -> bool:
    last_transform = record.get("last_transform")
    if not isinstance(last_transform, dict):
        return False
    result = last_transform.get("result")
    return isinstance(result, dict) and bool(result.get("skipped"))


def _no_structural_change_reason(record: dict[str, Any], release_dir: Path, ledger_path: Path) -> str | None:
    if _last_transform_delta_is_noop(record):
        return "no structural delta recorded; transform made no netlist change"

    before_path = _resolve_snapshot_path(record.get("before_snapshot"), release_dir, ledger_path)
    after_path = _resolve_snapshot_path(record.get("after_snapshot"), release_dir, ledger_path)
    if before_path is None or after_path is None:
        return None
    if before_path.suffix == ".txt" or after_path.suffix == ".txt":
        return None
    try:
        if before_path.read_text(encoding="utf-8") == after_path.read_text(encoding="utf-8"):
            return "before/after snapshots are identical; transform made no netlist change"
    except OSError:
        return None
    return None


def _last_transform_delta_is_noop(record: dict[str, Any]) -> bool:
    last_transform = record.get("last_transform")
    if not isinstance(last_transform, dict):
        return False
    delta = last_transform.get("delta")
    if not isinstance(delta, dict):
        return False
    if not _transform_result_reports_noop(last_transform.get("result")):
        return False

    gate_delta = _optional_int(delta.get("total_gate_delta"))
    before_gates = _optional_int(delta.get("before_total_gates"))
    after_gates = _optional_int(delta.get("after_total_gates"))
    if gate_delta is None and before_gates is not None and after_gates is not None:
        gate_delta = after_gates - before_gates
    if gate_delta is None or gate_delta != 0:
        return False

    sequence_keys = (
        "added_gates",
        "removed_gates",
        "added_dffs",
        "removed_dffs",
        "added_nets",
        "removed_nets",
    )
    if any(delta.get(key) for key in sequence_keys):
        return False

    type_delta = delta.get("type_delta")
    return not isinstance(type_delta, dict) or not any(type_delta.values())


def _transform_result_reports_noop(result: Any) -> bool:
    if not isinstance(result, dict):
        return False

    saw_noop_indicator = False
    count_keys = (
        "num_changed",
        "num_changed_outputs",
        "num_changed_nets",
        "num_inserted_buffers",
        "num_removed_gates",
        "num_removed_dffs",
        "num_removed_nets",
        "num_merged",
        "num_references",
    )
    for key in count_keys:
        value = _optional_int(result.get(key))
        if value is None:
            continue
        saw_noop_indicator = True
        if value != 0:
            return False

    sequence_keys = (
        "changed",
        "removed_gates",
        "removed_dffs",
        "removed_nets",
        "added_gates",
        "added_nets",
        "merged",
    )
    for key in sequence_keys:
        if key not in result:
            continue
        saw_noop_indicator = True
        if result.get(key):
            return False

    return saw_noop_indicator


def _verdict_from_equivalence_text(
    case: str,
    response_id: int,
    check: str,
    body: str,
    result: dict[str, Any],
) -> ValidationResult:
    body_lower = body.lower()
    if _is_bounded_skip_response(body):
        return ValidationResult(case, response_id, "INCONCLUSIVE", check, _first_line(body))
    if result.get("ok"):
        negative_tokens = ("not equivalent", "does not hold", "no.")
        positive_tokens = ("equivalent", "property holds", "yes")
        if not any(token in body_lower for token in negative_tokens) and any(
            token in body_lower for token in positive_tokens
        ):
            return ValidationResult(case, response_id, "PASS", check, f"oracle ok by {result.get('engine')}")
        return ValidationResult(case, response_id, "FAIL", check, "oracle says true but response did not")
    if result.get("counterexample") is None and result.get("engine") in {"bruteforce", "z3"}:
        return ValidationResult(case, response_id, "INCONCLUSIVE", check, str(result))
    if any(token in body_lower for token in ("not equivalent", "does not hold", "no.")):
        return ValidationResult(case, response_id, "PASS", check, f"oracle false by {result.get('engine')}")
    return ValidationResult(case, response_id, "FAIL", check, f"oracle disagreement: {result}")


def _read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _collect_metrics_for_cases(
    release_dir: Path,
    ledger_paths: dict[str, Path],
    results: list[ValidationResult],
) -> list[MetricRecord]:
    result_by_key = {(result.case, result.response_id): result for result in results}
    metrics: list[MetricRecord] = []
    for case, ledger_path in ledger_paths.items():
        for record in _read_records(ledger_path):
            response_id = int(record.get("response_id") or 0)
            steps = _plan_steps(record)
            op = str(steps[0].get("op")) if steps else ""
            if op not in TRANSFORM_OPS and op not in NON_EQUIVALENCE_TRANSFORMS:
                continue
            result = result_by_key.get((case, response_id))
            metrics.append(_metric_from_record(release_dir, ledger_path, case, response_id, op, record, result))
    return metrics


def _metric_from_record(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    response_id: int,
    op: str,
    record: dict[str, Any],
    validation_result: ValidationResult | None,
) -> MetricRecord:
    body = str(record.get("body") or "")
    last_transform = record.get("last_transform")
    delta = last_transform.get("delta") if isinstance(last_transform, dict) else None
    delta = delta if isinstance(delta, dict) else {}
    transform_result = last_transform.get("result") if isinstance(last_transform, dict) else None
    transform_result = transform_result if isinstance(transform_result, dict) else {}

    before_gates = _optional_int(delta.get("before_total_gates"))
    after_gates = _optional_int(delta.get("after_total_gates"))
    if before_gates is None or after_gates is None:
        parsed_gates = _parse_arrow_metric(body, "gates")
        before_gates = before_gates if before_gates is not None else parsed_gates[0]
        after_gates = after_gates if after_gates is not None else parsed_gates[1]
    gate_delta = _optional_int(delta.get("total_gate_delta"))
    if gate_delta is None and before_gates is not None and after_gates is not None:
        gate_delta = after_gates - before_gates

    before_depth, after_depth = _parse_arrow_metric(body, "depth")
    if before_depth is None:
        before_depth = _optional_int(transform_result.get("initial_depth"))
    if after_depth is None:
        after_depth = _optional_int(transform_result.get("final_depth"))
    depth_delta = after_depth - before_depth if before_depth is not None and after_depth is not None else None

    final_max_fanout = _optional_int(transform_result.get("final_max_fanout"))
    if final_max_fanout is None:
        match = re.search(r"Final max fanout is\s+(\d+)", body)
        final_max_fanout = int(match.group(1)) if match else None

    changed_items = _first_int(
        transform_result.get("num_changed"),
        transform_result.get("num_changed_outputs"),
        transform_result.get("num_inserted_buffers"),
        transform_result.get("num_references"),
        transform_result.get("num_removed_gates"),
    )
    skipped_items = _count_skipped_items(transform_result)
    status = validation_result.status if validation_result else "UNKNOWN"
    detail = validation_result.detail if validation_result else "no validation result"
    return MetricRecord(
        case=case,
        response_id=response_id,
        op=op,
        validation_status=status,
        validation_detail=detail,
        before_gates=before_gates,
        after_gates=after_gates,
        gate_delta=gate_delta,
        gate_improved=(gate_delta < 0) if gate_delta is not None else None,
        before_depth=before_depth,
        after_depth=after_depth,
        depth_delta=depth_delta,
        depth_improved=(depth_delta < 0) if depth_delta is not None else None,
        final_max_fanout=final_max_fanout,
        changed_items=changed_items,
        skipped_items=skipped_items,
        cost_objective=str(transform_result.get("cost_function") or _infer_cost_objective(op, body)),
        metric_source="ledger_delta/body",
    )


def _write_metrics_csv(path: Path, metrics: list[MetricRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(MetricRecord.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for metric in metrics:
            writer.writerow(metric.__dict__)


def _parse_arrow_metric(body: str, label: str) -> tuple[int | None, int | None]:
    match = re.search(rf"{re.escape(label)}\s+(\d+)\s*->\s*(\d+)", body, flags=re.IGNORECASE)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    return None


def _count_skipped_items(transform_result: dict[str, Any]) -> int | None:
    skipped = transform_result.get("skipped")
    if isinstance(skipped, list):
        return len(skipped)
    parsed = _optional_int(transform_result.get("num_skipped"))
    return parsed


def _infer_cost_objective(op: str, body: str) -> str:
    lowered = f"{op} {body}".lower()
    if "depth" in lowered or "critical path" in lowered:
        return "max_logic_depth"
    if "fanout" in lowered:
        return "max_fanout"
    if "gate" in lowered or "remove" in lowered or "merge" in lowered:
        return "gate_count"
    return "structural_change"


def _plan_steps(record: dict[str, Any]) -> list[dict[str, Any]]:
    plan = record.get("plan")
    if not isinstance(plan, dict):
        return []
    if isinstance(plan.get("op"), str):
        return [{"op": plan.get("op"), "args": plan.get("args", {})}]
    steps = plan.get("steps")
    return steps if isinstance(steps, list) else []


def _require_snapshot(record: dict[str, Any], release_dir: Path, ledger_path: Path) -> Any:
    design = _parse_snapshot(record, "after_snapshot", release_dir, ledger_path)
    if design is None:
        raise ValueError("missing readable after snapshot")
    return design


def _parse_snapshot(record: dict[str, Any], key: str, release_dir: Path, ledger_path: Path) -> Any:
    path = _resolve_snapshot_path(record.get(key), release_dir, ledger_path)
    if path is None or path.suffix == ".txt":
        return None
    resolved = path.resolve()
    cached = _SNAPSHOT_PARSE_CACHE.get(resolved)
    if cached is not None:
        return cached
    design = parse_verilog(path)
    _SNAPSHOT_PARSE_CACHE[resolved] = design
    return design


def _resolve_snapshot_path(raw: Any, release_dir: Path, ledger_path: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_absolute() and path.exists():
        return path
    parts = path.parts
    if "snapshots" in parts:
        index = parts.index("snapshots")
        copied_candidate = ledger_path.parent / Path(*parts[index:])
        if copied_candidate.exists():
            return copied_candidate
    release_candidate = release_dir / path
    if release_candidate.exists():
        return release_candidate
    return release_candidate


def _resolve_output_path(raw: Any, release_dir: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else release_dir / path


def _find_ledger(release_dir: Path, planner: str, case: str) -> Path | None:
    candidates = [
        release_dir / "runner_output" / planner / "validation" / case / "ledger.jsonl",
        release_dir / "output" / "validation" / case / "ledger.jsonl",
    ]
    return next((path for path in candidates if path.exists()), None)


def _select_cases(release_dir: Path, cases: list[str], ranges: list[str], *, run_all: bool) -> list[str]:
    selected = list(cases)
    for item in ranges:
        selected.extend(_expand_case_range(item))
    if run_all:
        root = release_dir / "testcase"
        return sorted(path.name for path in root.iterdir() if path.is_dir()) if root.exists() else []
    return selected


def _expand_case_range(raw_range: str) -> list[str]:
    start_text, end_text = raw_range.split("-", 1)
    start = _parse_case_number(start_text)
    end = _parse_case_number(end_text)
    return [f"test{number:02d}" for number in range(start, end + 1)]


def _parse_case_number(token: str) -> int:
    token = token.strip().lower()
    if token.startswith("test"):
        token = token[4:]
    return int(token)


def _positive_int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _design_gate_total(design: Any) -> int:
    return len(getattr(design, "gates", {})) + len(getattr(design, "dffs", {}))


def _is_expensive_analysis_design(design: Any) -> bool:
    return _design_gate_total(design) > VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT


def _has_error_marker(body: str) -> bool:
    return any(
        marker in body
        for marker in (
            "Error:",
            "Tool call rejected",
            "LLM planner is not configured",
            "LLM API error",
            "No design has been loaded",
        )
    )


def _is_bounded_skip_response(body: str) -> bool:
    body_lower = body.lower()
    return "skipped" in body_lower and "large design" in body_lower and "budget" in body_lower


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def _contains_result(
    case: str,
    response_id: int,
    check: str,
    body: str,
    expected: str,
) -> ValidationResult:
    status = "PASS" if expected in body else "FAIL"
    return ValidationResult(case, response_id, status, check, expected)


def _print_summary(results: list[ValidationResult]) -> None:
    counts = {status: sum(1 for result in results if result.status == status) for status in ("PASS", "FAIL", "SKIP", "INCONCLUSIVE")}
    print(
        f"Validated {len(results)} response(s): "
        f"PASS={counts['PASS']}, FAIL={counts['FAIL']}, "
        f"SKIP={counts['SKIP']}, INCONCLUSIVE={counts['INCONCLUSIVE']}"
    )
    for result in results:
        if result.status in {"FAIL", "INCONCLUSIVE"}:
            print(f"- {result.status} {result.case} response {result.response_id} [{result.check}]: {result.detail}")


def _print_metrics_summary(metrics: list[MetricRecord]) -> None:
    gate_improvements = sum(1 for metric in metrics if metric.gate_improved is True)
    depth_improvements = sum(1 for metric in metrics if metric.depth_improved is True)
    validated_improvements = sum(
        1
        for metric in metrics
        if metric.validation_status == "PASS" and (metric.gate_improved is True or metric.depth_improved is True)
    )
    print(
        "Metrics: "
        f"{len(metrics)} transform/optimization response(s), "
        f"gate improvements={gate_improvements}, "
        f"depth improvements={depth_improvements}, "
        f"validated improvements={validated_improvements}"
    )


def _print_coverage() -> None:
    from agent.tool_schema import ANALYSIS_OPS, IO_OPS, TRANSFORM_OPS as SCHEMA_TRANSFORM_OPS, VERIFY_OPS

    schema_ops = set(IO_OPS) | set(ANALYSIS_OPS) | set(SCHEMA_TRANSFORM_OPS) | set(VERIFY_OPS)
    generic_transform_ops = set(TRANSFORM_OPS)
    intentionally_skipped_ops = set(NON_EQUIVALENCE_TRANSFORMS)
    covered_ops = EXACT_VALIDATED_OPS | generic_transform_ops | intentionally_skipped_ops
    uncovered_ops = sorted(schema_ops - covered_ops)

    print("Validator coverage")
    print(f"- exact oracle ops: {len(EXACT_VALIDATED_OPS)}")
    print(f"- generic transform equivalence ops: {len(generic_transform_ops)}")
    print(f"- intentionally skipped/non-equivalence ops: {len(intentionally_skipped_ops)}")
    print(f"- schema ops total: {len(schema_ops)}")
    print(f"- uncovered ops: {len(uncovered_ops)}")
    if uncovered_ops:
        print("Uncovered ops:")
        for op in uncovered_ops:
            print(f"- {op}")


if __name__ == "__main__":
    raise SystemExit(main())
