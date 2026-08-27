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
from eda.graph import rebuild_graph
from parser.verilog_parser import parse_verilog
from runtime.limits import DEFAULT_COMPLETE_PATH_LIMIT

try:
    from tqdm import tqdm as _tqdm
except Exception:  # pragma: no cover - tqdm is optional at runtime.
    _tqdm = None


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
    "replace_nand_const1_with_not",
}
VALIDATOR_FULL_TRANSFORM_EQ_GATE_LIMIT = 6000
VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT = 20000
VALIDATOR_LARGE_SELECTED_OUTPUT_LIMIT = 8
VALIDATOR_DEEP_LARGE_CHECKS = False
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


def _progress(
    iterable: Any,
    *,
    enabled: bool,
    desc: str,
    unit: str,
    leave: bool = True,
) -> Any:
    if enabled and _tqdm is not None:
        return _tqdm(iterable, desc=desc, unit=unit, leave=leave)
    return iterable


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release runner outputs using ledger snapshots and external EDA checks."
    )
    parser.add_argument("--release-dir", type=Path, default=Path("release_0706"))
    parser.add_argument("--planner", default="rule")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--case-range", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--existing-ledgers",
        action="store_true",
        help=(
            "Validate only cases that already have validation ledgers. "
            "Useful for partial release runs; plain --all remains strict."
        ),
    )
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
    parser.add_argument(
        "--deep-large-checks",
        action="store_true",
        help=(
            "Spend extra time on large transforms by attempting broader/full-output equivalence "
            "after cheaper guards are insufficient or only partial."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bars.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSONL validation report path.")
    parser.add_argument(
        "--results-csv-output",
        type=Path,
        help="Optional CSV report with one row for every validated response.",
    )
    parser.add_argument(
        "--metrics-output",
        type=Path,
        help="Optional CSV report with transform/optimization QoR metrics.",
    )
    args = parser.parse_args()

    if args.show_coverage:
        _print_coverage()
        return 0

    global VALIDATOR_DEEP_LARGE_CHECKS
    VALIDATOR_DEEP_LARGE_CHECKS = bool(args.deep_large_checks)

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = (repo_root / args.release_dir).resolve()
    cases = _select_cases(release_dir, args.case, args.case_range, run_all=args.all)
    if args.existing_ledgers:
        if cases:
            cases = [case for case in cases if _find_ledger(release_dir, args.planner, case) is not None]
        else:
            cases = _select_existing_ledger_cases(release_dir, args.planner)
    if not cases:
        print(
            "No cases selected. Use --all, --existing-ledgers, --case testNN, or --case-range test25-test40.",
            file=sys.stderr,
        )
        return 2

    results: list[ValidationResult] = []
    ledger_paths: dict[str, Path] = {}
    show_progress = not args.no_progress
    for case in _progress(cases, enabled=show_progress, desc="cases", unit="case"):
        ledger_path = _find_ledger(release_dir, args.planner, case)
        if ledger_path is None:
            results.append(ValidationResult(case, 0, "FAIL", "ledger", "ledger.jsonl not found"))
            continue
        ledger_paths[case] = ledger_path
        results.extend(_validate_ledger(release_dir, ledger_path, case, show_progress=show_progress))

    _print_summary(results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "".join(json.dumps(result.__dict__, sort_keys=True) + "\n" for result in results),
            encoding="utf-8",
        )
    if args.results_csv_output:
        _write_results_csv(args.results_csv_output, results)
    if args.metrics_output:
        metrics = _collect_metrics_for_cases(release_dir, ledger_paths, results)
        _write_metrics_csv(args.metrics_output, metrics)
        _print_metrics_summary(metrics)

    has_fail = any(result.status == "FAIL" for result in results)
    has_inconclusive = any(result.status == "INCONCLUSIVE" for result in results)
    return 1 if has_fail or (args.fail_on_inconclusive and has_inconclusive) else 0


def _validate_ledger(
    release_dir: Path,
    ledger_path: Path,
    case: str,
    *,
    show_progress: bool = False,
) -> list[ValidationResult]:
    _SNAPSHOT_PARSE_CACHE.clear()
    records = _read_records(ledger_path)
    original_design = None
    results: list[ValidationResult] = []
    iterator = _progress(records, enabled=show_progress, desc=case, unit="response", leave=False)
    for record in iterator:
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
    if _is_expensive_analysis_design(design) and _path_relevant_gate_count(design, src, dst) > VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT:
        reachability_probe = all_paths(design, src=src, dst=dst, max_paths=1)
        if not reachability_probe.get("truncated") and int(reachability_probe.get("num_paths") or 0) == 0:
            expected = f'Combinational paths from "{src}" to "{dst}": 0'
            status = "PASS" if expected in body else "FAIL"
            return ValidationResult(case, response_id, status, "report_all_paths", "no path exists by reachability")
        if f'Combinational paths from "{src}" to "{dst}":' in body:
            return ValidationResult(case, response_id, "INCONCLUSIVE", "report_all_paths", "large-design all-path oracle skipped")
    result = all_paths(design, src=src, dst=dst, max_paths=max_paths)
    if int(result.get("num_paths") or 0) >= max_paths:
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "report_all_paths",
            f"bounded all-path oracle reached max_paths={max_paths}",
        )
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
    result = cut_signal_between_pi_po(design, signal)
    if result.get("truncated"):
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "check_cut_signal",
            f'cut oracle reached pair-search bound after {result.get("checked_pairs", 0)} pair(s)',
        )
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
    result = derive_boolean_equation(design, target, max_terms=5000)
    final_ref = str(result["expression"])
    expected_inline = f'Boolean equation for "{target}": {target} = {final_ref}'
    expected_report_line = f"Final expression reference: {final_ref}"
    equations = result.get("equations") or []
    if not equations:
        status = "PASS" if expected_inline in body else "FAIL"
        return ValidationResult(case, response_id, status, "derive_boolean_equation", f"final_reference: {final_ref}")

    if expected_report_line not in body or f"Equation count: {len(equations)}." not in body:
        return ValidationResult(
            case,
            response_id,
            "FAIL",
            "derive_boolean_equation",
            "stdout equation count or final reference does not match the structural DAG oracle",
        )
    report_path = _boolean_equation_report_path(release_dir, body)
    if report_path is None or not report_path.is_file():
        return ValidationResult(case, response_id, "FAIL", "derive_boolean_equation", "Boolean-equation report file is missing")

    expected_lines = _expected_boolean_equation_report_lines(result)
    actual_lines = report_path.read_text(encoding="utf-8").splitlines()
    if actual_lines != expected_lines:
        mismatch = next(
            (
                index
                for index, (actual, expected) in enumerate(zip(actual_lines, expected_lines), 1)
                if actual != expected
            ),
            min(len(actual_lines), len(expected_lines)) + 1,
        )
        return ValidationResult(
            case,
            response_id,
            "FAIL",
            "derive_boolean_equation",
            f"Boolean-equation report differs from the structural DAG oracle at line {mismatch}",
        )
    return ValidationResult(
        case,
        response_id,
        "PASS",
        "derive_boolean_equation",
        f"complete {len(equations)}-equation DAG matches gates, operators, feedback defaults, and final reference",
    )


def _boolean_equation_report_path(release_dir: Path, body: str) -> Path | None:
    match = re.search(r"written to\s+(.+?)\.\s*(?:\r?\n|$)", body)
    if match is None:
        return None
    raw_path = match.group(1).strip().strip('"')
    candidate = Path(raw_path.replace("\\", "/"))
    resolved = candidate.resolve() if candidate.is_absolute() else (release_dir / candidate).resolve()
    try:
        resolved.relative_to(release_dir.resolve())
    except ValueError:
        return None
    return resolved


def _expected_boolean_equation_report_lines(result: dict[str, Any]) -> list[str]:
    equations = result.get("equations") or []
    feedback_defaults = result.get("sequential_feedback_defaults") or []
    lines = [
        f'Boolean equation DAG for "{result["target"]}"',
        "boundary: primary inputs and constants",
        "DFF handling: DFF Q references are expanded to their D input cones",
        "sequential feedback default: 1'b0",
        f"equation_count: {len(equations)}",
    ]
    if feedback_defaults:
        lines.append("feedback_defaults:")
        lines.extend(f'- {item["net"]} = {item["value"]}' for item in feedback_defaults)
    lines.append("equations:")
    lines.extend(
        f'{index}. {item["net"]} = {item["expr"]} # gate={item["gate"]}, type={item["type"]}'
        for index, item in enumerate(equations, 1)
    )
    if not equations:
        lines.append("- none")
    lines.extend(["final:", f'final_reference: {result["expression"]}'])
    return lines


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
    certificate = _last_transform_structural_certificate(record, before, after)
    if certificate is not None:
        if _is_bounded_skip_response(body):
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                "check_equivalent_to_last_transform_input",
                _first_line(body),
            )
        status = "PASS" if "Equivalent to the pre-transformation netlist." in body else "FAIL"
        return ValidationResult(
            case,
            response_id,
            status,
            "check_equivalent_to_last_transform_input",
            certificate,
        )
    if _is_bounded_skip_response(body):
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            "check_equivalent_to_last_transform_input",
            _first_line(body),
        )
    result = check_design_equivalence(before, after)
    return _verdict_from_equivalence_text(case, response_id, "check_equivalent_to_last_transform_input", body, result)


def _last_transform_structural_certificate(record: dict[str, Any], before: Any, after: Any) -> str | None:
    if _designs_match_with_net_map(before, after, {}):
        return "exact structural identity certificate"
    last_transform = record.get("last_transform")
    if not isinstance(last_transform, dict):
        return None
    result = last_transform.get("result")
    if not isinstance(result, dict):
        return None
    if last_transform.get("transform") == "rename_net":
        old_net = result.get("old_net")
        new_net = result.get("new_net")
        if not isinstance(old_net, str) or not isinstance(new_net, str):
            return None
        if _designs_match_with_net_map(before, after, {old_net: new_net}):
            return f'exact alpha-equivalence certificate for net "{old_net}" -> "{new_net}"'
    if last_transform.get("transform") == "rename_gate":
        old_name = result.get("old_name")
        new_name = result.get("new_name")
        if not isinstance(old_name, str) or not isinstance(new_name, str):
            return None
        if _designs_match_with_instance_rename(before, after, old_name, new_name):
            return f'exact alpha-equivalence certificate for instance "{old_name}" -> "{new_name}"'
    return None


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
        compositional = _check_large_compositional_transform(before, after, op, args)
        if compositional is not None:
            if compositional[0] != "PASS":
                return ValidationResult(case, response_id, compositional[0], op, compositional[1])
            connectivity = _connectivity_regression(check_connectivity(before), check_connectivity(after))
            if not connectivity.get("ok", False):
                return ValidationResult(
                    case,
                    response_id,
                    "FAIL",
                    op,
                    f"large-design connectivity regression failed: {connectivity}",
                )
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity regression passed; {compositional[1]}",
            )
        rewrite_certificate = _check_rewrite_log_certificate(before, after, op, record)
        if rewrite_certificate is not None:
            if rewrite_certificate[0] != "PASS":
                return ValidationResult(case, response_id, rewrite_certificate[0], op, rewrite_certificate[1])
            connectivity = _connectivity_regression(check_connectivity(before), check_connectivity(after))
            if not connectivity.get("ok", False):
                return ValidationResult(
                    case,
                    response_id,
                    "FAIL",
                    op,
                    f"large-design connectivity regression failed: {connectivity}",
                )
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity regression passed; {rewrite_certificate[1]}",
            )
        residual = _check_transform_residual(after, op, residual_args)
        if residual is not None and residual[0] == "FAIL":
            return ValidationResult(case, response_id, residual[0], op, residual[1])
        if residual is not None and residual[0] == "INCONCLUSIVE":
            return ValidationResult(case, response_id, residual[0], op, residual[1])
        if residual is not None and residual[0] == "PASS":
            connectivity = _connectivity_regression(check_connectivity(before), check_connectivity(after))
            if not connectivity.get("ok", False):
                return ValidationResult(
                    case,
                    response_id,
                    "FAIL",
                    op,
                    f"large-design connectivity regression failed: {connectivity}",
                )
            if VALIDATOR_DEEP_LARGE_CHECKS:
                return _deep_large_transform_equivalence_result(
                    case,
                    response_id,
                    op,
                    before,
                    after,
                    prefix=f"large-design connectivity regression passed; {residual[1]}; ",
                )
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity regression passed; {residual[1]}",
            )
        if no_change_reason is not None and op in NOOP_ACCEPTABLE_TRANSFORMS:
            return ValidationResult(case, response_id, "PASS", op, no_change_reason)
        if _is_bounded_skip_response(body):
            return ValidationResult(case, response_id, "INCONCLUSIVE", op, _first_line(body))
        return _validate_large_transform_with_guards(
            case,
            response_id,
            op,
            _large_transform_equivalence_args(args, record),
            before,
            after,
        )

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
    connectivity = _connectivity_regression(check_connectivity(before), check_connectivity(after))
    if not connectivity.get("ok", False):
        return ValidationResult(
            case,
            response_id,
            "FAIL",
            op,
            f"large-design connectivity regression failed: {connectivity}",
        )

    compositional = _check_large_compositional_transform(before, after, op, args)
    if compositional is not None:
        return ValidationResult(
            case,
            response_id,
            compositional[0],
            op,
            f"large-design connectivity regression passed; {compositional[1]}",
        )

    if op == "remove_dangling":
        result = check_design_equivalence(before, after)
        if result.get("ok"):
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity regression passed; full-output equivalence passed by {result.get('engine')}",
            )
        if _equivalence_depends_on_unknown_constant(result):
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                op,
                f"full-output equivalence depends on unknown/X constant: {result}",
            )
        return ValidationResult(case, response_id, "FAIL", op, f"full-output equivalence failed: {result}")

    selected_outputs = _selected_outputs_for_large_transform(before, after, args)
    if selected_outputs:
        result = check_design_equivalence(before, after, outputs=selected_outputs)
        if result.get("ok"):
            if VALIDATOR_DEEP_LARGE_CHECKS:
                return _deep_large_transform_equivalence_result(
                    case,
                    response_id,
                    op,
                    before,
                    after,
                    prefix=(
                        "large-design connectivity regression check passed; "
                        f"selected-output equivalence passed for {selected_outputs}; "
                    ),
                )
            return ValidationResult(
                case,
                response_id,
                "PASS",
                op,
                f"large-design connectivity regression check passed; selected-output equivalence passed for {selected_outputs}",
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

    if VALIDATOR_DEEP_LARGE_CHECKS:
        return _deep_large_transform_equivalence_result(
            case,
            response_id,
            op,
            before,
            after,
            prefix="large-design connectivity regression check passed; selected-output target unavailable; ",
        )

    return ValidationResult(
        case,
        response_id,
        "INCONCLUSIVE",
        op,
        (
            "large-design connectivity regression check passed; selected-output equivalence target unavailable, "
            "so full transform equivalence remains bounded"
        ),
    )


def _deep_large_transform_equivalence_result(
    case: str,
    response_id: int,
    op: str,
    before: Any,
    after: Any,
    *,
    prefix: str,
) -> ValidationResult:
    result = check_design_equivalence(before, after)
    if result.get("ok"):
        return ValidationResult(
            case,
            response_id,
            "PASS",
            op,
            f"{prefix}deep full-output equivalence passed by {result.get('engine')}",
        )
    if _equivalence_depends_on_unknown_constant(result):
        return ValidationResult(
            case,
            response_id,
            "INCONCLUSIVE",
            op,
            f"{prefix}deep full-output equivalence depends on unknown/X constant: {result}",
        )
    failures = result.get("failures")
    if isinstance(failures, dict):
        solver_inconclusive = any(
            isinstance(failure, dict)
            and failure.get("counterexample") is None
            and failure.get("engine") in {"bruteforce", "z3"}
            for failure in failures.values()
        )
        if solver_inconclusive:
            return ValidationResult(
                case,
                response_id,
                "INCONCLUSIVE",
                op,
                f"{prefix}deep full-output equivalence remained inconclusive: {result}",
            )
    return ValidationResult(case, response_id, "FAIL", op, f"{prefix}deep full-output equivalence failed: {result}")


def _large_transform_equivalence_args(args: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    equivalence_args = dict(args)
    outputs = list(equivalence_args.get("outputs") or []) if isinstance(equivalence_args.get("outputs"), list) else []
    last_transform = record.get("last_transform")
    result = last_transform.get("result") if isinstance(last_transform, dict) else None
    if isinstance(result, dict):
        attempted_outputs = result.get("attempted_outputs")
        if isinstance(attempted_outputs, list):
            outputs.extend(str(item) for item in attempted_outputs)
        changed = result.get("changed")
        if isinstance(changed, list):
            for item in changed:
                if not isinstance(item, dict):
                    continue
                for key in ("output", "target", "resolved_target"):
                    value = item.get(key)
                    if isinstance(value, str):
                        outputs.append(value)
    if outputs:
        equivalence_args["outputs"] = sorted(dict.fromkeys(outputs))
    return equivalence_args


def _connectivity_regression(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return only connectivity problems introduced by a candidate transform."""
    before_missing = set(before.get("missing_drivers", []))
    after_missing = set(after.get("missing_drivers", []))
    new_missing = sorted(after_missing - before_missing)

    before_duplicates = {
        net: sorted(drivers)
        for net, drivers in (before.get("duplicate_drivers") or {}).items()
    }
    after_duplicates = {
        net: sorted(drivers)
        for net, drivers in (after.get("duplicate_drivers") or {}).items()
    }
    new_duplicates = {
        net: drivers
        for net, drivers in sorted(after_duplicates.items())
        if net not in before_duplicates or drivers != before_duplicates[net]
    }

    return {
        "ok": not new_missing and not new_duplicates,
        "missing_drivers": sorted(after_missing),
        "duplicate_drivers": after_duplicates,
        "new_missing_drivers": new_missing,
        "new_duplicate_drivers": new_duplicates,
    }


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
    for candidate in list(candidates):
        if candidate not in common_outputs:
            candidates.extend(_fanout_equivalence_endpoints(before, after, candidate))
    selected = [
        candidate
        for candidate in candidates
        if candidate in common_outputs or _is_common_dff_input_endpoint(before, after, candidate)
    ]
    return sorted(dict.fromkeys(selected))[:VALIDATOR_LARGE_SELECTED_OUTPUT_LIMIT]


def _check_transform_residual(design: Any, op: str, args: dict[str, Any]) -> tuple[str, str] | None:
    if op == "insert_buffers_for_all_high_fanout":
        if args.get("_bounded_partial"):
            return ("INCONCLUSIVE", "bounded fanout insertion skipped some high-fanout nets")
        max_fanout = int(args.get("max_fanout") or 0)
        result = check_fanout(design, max_fanout)
        return ("PASS", f"fanout <= {max_fanout}") if result.get("ok") else ("FAIL", f"fanout violations: {result}")
    if op == "replace_or_with_nand_not":
        target = str(args.get("cone_target") or "")
        if not target:
            return ("FAIL", "replace-or residual check is missing cone_target")
        try:
            cone_gates = logic_cone(design, target)
        except Exception as exc:
            return ("INCONCLUSIVE", f"replace-or residual cone unavailable: {exc}")
        remaining = sorted(
            name
            for name in cone_gates
            if name in getattr(design, "gates", {}) and design.gates[name].type == "or"
        )
        if remaining:
            return ("FAIL", f"{len(remaining)} OR gate(s) remain in target cone")
        return ("PASS", f'no OR gates remain in cone of "{target}"')
    if op in {"replace_xor_with_nand", "replace_xnor_with_nor", "replace_xnor_nor_with_basic_gates"}:
        forbidden = "xor" if op == "replace_xor_with_nand" else "xnor"
        count = gate_type_count(design, forbidden)["count"]
        return ("PASS", f"{forbidden} count is 0") if count == 0 else ("FAIL", f"{forbidden} count remains {count}")
    if op == "replace_and_not_with_nand":
        return _check_target_gate_library(design, {"nand", "not"})
    if op == "replace_with_and_not":
        return _check_target_gate_library(design, {"and", "not"})
    if op == "collapse_back_to_back_inverters":
        count = _count_collapsible_inverter_pairs(design)
        if count == 0:
            return ("PASS", "no collapsible back-to-back inverter pairs remain")
        return ("FAIL", f"{count} collapsible back-to-back inverter pair(s) remain")
    return None


def _check_target_gate_library(design: Any, allowed_types: set[str]) -> tuple[str, str]:
    unexpected = sorted({gate.type for gate in getattr(design, "gates", {}).values()} - allowed_types)
    library = "/".join(sorted(allowed_types))
    if unexpected:
        return ("FAIL", f"unexpected combinational gate type(s) remain outside {library}: {unexpected}")
    return ("PASS", f"all combinational gates use only {library}")


def _check_large_compositional_transform(
    before: Any,
    after: Any,
    op: str,
    args: dict[str, Any],
) -> tuple[str, str] | None:
    if op == "rename_net":
        old_net = str(args.get("old_net") or "")
        new_net = str(args.get("new_net") or "")
        if not old_net or not new_net:
            return ("FAIL", "rename-net proof is missing old_net or new_net")
        if _designs_match_with_net_map(before, after, {old_net: new_net}):
            return ("PASS", f'exact alpha-equivalence proved for net rename "{old_net}" -> "{new_net}"')
        return ("FAIL", "before/after designs differ beyond the requested net rename")

    if op == "rename_gate":
        old_name = str(args.get("old_name") or "")
        new_name = str(args.get("new_name") or "")
        if not old_name or not new_name:
            return ("FAIL", "rename-gate proof is missing old_name or new_name")
        if _designs_match_with_instance_rename(before, after, old_name, new_name):
            return ("PASS", f'exact alpha-equivalence proved for instance rename "{old_name}" -> "{new_name}"')
        return ("FAIL", "before/after designs differ beyond the requested instance rename")

    if op == "remove_dangling":
        if _same_logic_ignoring_unused_wire_declarations(before, after):
            removed = len(set(before.wires) - set(after.wires))
            return ("PASS", f"logic and ports are identical; only {removed} unused wire declaration(s) were removed")
        return None

    if op == "replace_or_with_nand_not":
        target = str(args.get("cone_target") or "")
        if not target:
            return ("FAIL", "replace-or proof is missing cone_target")
        return _check_replace_or_with_nand_not_certificate(before, after, target)

    if op == "insert_dedicated_buffers_for_each_load":
        target = str(args.get("net") or "")
        if not target:
            return ("FAIL", "dedicated-buffer proof is missing target net")
        return _check_dedicated_buffer_identity(before, after, target)

    return None


def _designs_match_with_net_map(before: Any, after: Any, net_map: dict[str, str]) -> bool:
    def mapped(net: str | None) -> str | None:
        return net_map.get(net, net) if net is not None else None

    if before.module_name != after.module_name:
        return False
    if {mapped(net) for net in before.inputs} != set(after.inputs):
        return False
    if {mapped(net) for net in before.outputs} != set(after.outputs):
        return False
    if {mapped(net) for net in before.wires} != set(after.wires):
        return False
    if set(before.gates) != set(after.gates) or set(before.dffs) != set(after.dffs):
        return False

    for name, gate in before.gates.items():
        candidate = after.gates[name]
        if (
            gate.type != candidate.type
            or [mapped(net) for net in gate.inputs] != candidate.inputs
            or mapped(gate.output) != candidate.output
            or gate.attrs != candidate.attrs
        ):
            return False
    for name, dff in before.dffs.items():
        candidate = after.dffs[name]
        if (
            mapped(dff.d) != candidate.d
            or mapped(dff.q) != candidate.q
            or mapped(dff.clk) != candidate.clk
            or mapped(dff.rst) != candidate.rst
            or mapped(dff.set_signal) != candidate.set_signal
            or dff.rst_value != candidate.rst_value
            or dff.attrs != candidate.attrs
        ):
            return False
    return True


def _designs_match_with_instance_rename(before: Any, after: Any, old_name: str, new_name: str) -> bool:
    if before.module_name != after.module_name:
        return False
    if set(before.inputs) != set(after.inputs) or set(before.outputs) != set(after.outputs):
        return False
    if set(before.wires) != set(after.wires):
        return False

    gate_name_map = {old_name: new_name} if old_name in before.gates else {}
    dff_name_map = {old_name: new_name} if old_name in before.dffs else {}
    if old_name not in before.gates and old_name not in before.dffs:
        return False
    if new_name in before.gates or new_name in before.dffs:
        return False

    expected_gates = {gate_name_map.get(name, name) for name in before.gates}
    expected_dffs = {dff_name_map.get(name, name) for name in before.dffs}
    if expected_gates != set(after.gates) or expected_dffs != set(after.dffs):
        return False

    for name, gate in before.gates.items():
        candidate = after.gates[gate_name_map.get(name, name)]
        if (
            gate.type != candidate.type
            or gate.inputs != candidate.inputs
            or gate.output != candidate.output
            or gate.attrs != candidate.attrs
        ):
            return False
    for name, dff in before.dffs.items():
        candidate = after.dffs[dff_name_map.get(name, name)]
        if (
            dff.d != candidate.d
            or dff.q != candidate.q
            or dff.clk != candidate.clk
            or dff.rst != candidate.rst
            or dff.set_signal != candidate.set_signal
            or dff.rst_value != candidate.rst_value
            or dff.attrs != candidate.attrs
        ):
            return False
    return True


def _check_replace_or_with_nand_not_certificate(before: Any, after: Any, target: str) -> tuple[str, str]:
    if before.module_name != after.module_name or set(before.inputs) != set(after.inputs) or set(before.outputs) != set(after.outputs):
        return ("FAIL", "replace-or certificate saw module or port changes")
    if set(before.dffs) != set(after.dffs):
        return ("FAIL", "replace-or certificate saw DFF instance changes")
    for name, dff in before.dffs.items():
        candidate = after.dffs[name]
        if (
            dff.d != candidate.d
            or dff.q != candidate.q
            or dff.clk != candidate.clk
            or dff.rst != candidate.rst
            or dff.set_signal != candidate.set_signal
            or dff.rst_value != candidate.rst_value
            or dff.attrs != candidate.attrs
        ):
            return ("FAIL", f'DFF "{name}" changed outside replace-or template')

    try:
        cone_names = set(logic_cone(before, target))
    except Exception as exc:
        return ("INCONCLUSIVE", f"replace-or certificate cone unavailable: {exc}")

    added_names = set(after.gates) - set(before.gates)
    consumed_added: set[str] = set()
    rewritten = 0
    for name, gate in before.gates.items():
        candidate = after.gates.get(name)
        if candidate is None:
            return ("FAIL", f'gate "{name}" was removed')
        if name not in cone_names or gate.type != "or" or len(gate.inputs) != 2:
            if not _gate_matches(candidate, gate):
                return ("FAIL", f'gate "{name}" changed outside the OR-to-NAND/NOT template')
            continue

        if _gate_matches(candidate, gate):
            continue
        if candidate.type != "nand" or candidate.output != gate.output or len(candidate.inputs) != 2:
            return ("FAIL", f'OR gate "{name}" was not rewritten as a same-output NAND')

        expected_sources = list(gate.inputs)
        seen_sources: list[str] = []
        for not_net in candidate.inputs:
            not_gate_name = _single_added_not_driver(after, not_net, added_names)
            if not_gate_name is None:
                return ("FAIL", f'input "{not_net}" of rewritten gate "{name}" is not driven by one added NOT')
            not_gate = after.gates[not_gate_name]
            seen_sources.append(not_gate.inputs[0])
            consumed_added.add(not_gate_name)
        if sorted(seen_sources) != sorted(expected_sources):
            return ("FAIL", f'rewritten gate "{name}" NOT inputs do not match original OR inputs')
        rewritten += 1

    if consumed_added != added_names:
        extra = sorted(added_names - consumed_added)
        return ("FAIL", f"unexpected added gate(s) outside OR-to-NAND/NOT template: {extra}")
    if rewritten == 0:
        residual = _check_transform_residual(after, "replace_or_with_nand_not", {"cone_target": target})
        if residual is not None and residual[0] != "PASS":
            return residual
        return ("PASS", f'no OR gates required rewriting in cone of "{target}"')
    return ("PASS", f"{rewritten} OR gate rewrite template(s) proved by De Morgan structure")


def _single_added_not_driver(design: Any, net: str, added_names: set[str]) -> str | None:
    matches = [
        name
        for name in added_names
        if name in design.gates
        and design.gates[name].type == "not"
        and len(design.gates[name].inputs) == 1
        and design.gates[name].output == net
    ]
    return matches[0] if len(matches) == 1 else None


def _gate_matches(candidate: Any, expected: Any) -> bool:
    return (
        candidate.type == expected.type
        and candidate.inputs == expected.inputs
        and candidate.output == expected.output
        and candidate.attrs == expected.attrs
    )


def _check_rewrite_log_certificate(
    before: Any,
    after: Any,
    op: str,
    record: dict[str, Any],
) -> tuple[str, str] | None:
    if op not in {
        "replace_and_not_with_nand",
        "replace_with_and_not",
        "replace_xor_with_nand",
        "replace_xnor_with_nor",
        "replace_xnor_nor_with_basic_gates",
    }:
        return None
    last_transform = record.get("last_transform")
    if not isinstance(last_transform, dict):
        return None
    result = last_transform.get("result")
    if not isinstance(result, dict):
        return None
    changed = result.get("changed")
    if not isinstance(changed, list):
        return None

    if op == "replace_and_not_with_nand":
        allowed_types = {"nand", "not"}
    elif op == "replace_with_and_not":
        allowed_types = {"and", "not"}
    elif op == "replace_xor_with_nand":
        allowed_types = {"nand"}
    else:
        allowed_types = {"nor"}

    residual = _check_transform_residual(after, op, {})
    if residual is not None and residual[0] != "PASS":
        return residual
    library = _check_target_gate_library(after, allowed_types) if op in {"replace_and_not_with_nand", "replace_with_and_not"} else None
    if library is not None and library[0] != "PASS":
        return library

    changed_names = {str(item.get("rewritten_gate")) for item in changed if isinstance(item, dict)}
    changed_names.discard("")
    added_names: set[str] = set()
    for item in changed:
        if isinstance(item, dict) and isinstance(item.get("added_gates"), list):
            added_names.update(str(name) for name in item["added_gates"])
    delta = last_transform.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("added_gates"), list):
        added_names.update(str(name) for name in delta["added_gates"])
    if not _unchanged_outside_rewrite_log(before, after, changed_names, added_names):
        return ("FAIL", "rewrite-log certificate found structural changes outside the logged rewrite windows")

    for item in changed:
        if not isinstance(item, dict):
            return ("FAIL", "rewrite-log certificate contains a malformed changed item")
        gate_name = str(item.get("rewritten_gate") or "")
        old_gate = before.gates.get(gate_name)
        if old_gate is None:
            return ("FAIL", f'rewrite-log gate "{gate_name}" is absent from the before design')
        if str(item.get("old_type") or old_gate.type) != old_gate.type:
            return ("FAIL", f'rewrite-log old_type mismatch for "{gate_name}"')
        output = str(item.get("output") or old_gate.output)
        if output != old_gate.output:
            return ("FAIL", f'rewrite-log output mismatch for "{gate_name}"')
        local = _prove_local_rewrite_truth_table(after, output, old_gate.type, old_gate.inputs)
        if local is not None:
            return local

    return ("PASS", f"{len(changed)} logged rewrite(s) proved by local truth-table certificate")


def _unchanged_outside_rewrite_log(before: Any, after: Any, changed_names: set[str], added_names: set[str]) -> bool:
    if before.module_name != after.module_name or set(before.inputs) != set(after.inputs) or set(before.outputs) != set(after.outputs):
        return False
    if set(before.dffs) != set(after.dffs):
        return False
    for name, dff in before.dffs.items():
        candidate = after.dffs[name]
        if (
            dff.d != candidate.d
            or dff.q != candidate.q
            or dff.clk != candidate.clk
            or dff.rst != candidate.rst
            or dff.set_signal != candidate.set_signal
            or dff.rst_value != candidate.rst_value
            or dff.attrs != candidate.attrs
        ):
            return False
    if set(after.gates) - set(before.gates) != added_names:
        return False
    if set(before.gates) - set(after.gates):
        return False
    for name, gate in before.gates.items():
        if name in changed_names:
            continue
        if not _gate_matches(after.gates[name], gate):
            return False
    return True


def _prove_local_rewrite_truth_table(
    design: Any,
    output: str,
    old_type: str,
    old_inputs: list[str],
) -> tuple[str, str] | None:
    if len(old_inputs) > 4:
        return ("INCONCLUSIVE", f'local rewrite certificate for "{output}" has too many inputs')
    boundary = list(dict.fromkeys(old_inputs))
    for mask in range(1 << len(boundary)):
        assignment = {net: bool((mask >> index) & 1) for index, net in enumerate(boundary)}
        observed = _eval_local_net(design, output, assignment, set())
        if observed is None:
            return ("INCONCLUSIVE", f'local rewrite cone for "{output}" reaches outside the original gate boundary')
        expected = _eval_primitive_gate(old_type, [assignment[net] for net in old_inputs])
        if observed != expected:
            return ("FAIL", f'local rewrite truth table mismatch at "{output}"')
    return None


def _eval_local_net(design: Any, net: str, assignment: dict[str, bool], visiting: set[str]) -> bool | None:
    if net in assignment:
        return assignment[net]
    if _const_bool(net) is not None:
        return _const_bool(net)
    if net in visiting:
        return None
    driver = getattr(design, "drivers", {}).get(net)
    if driver is None:
        rebuild_graph(design)
        driver = getattr(design, "drivers", {}).get(net)
    if not driver or not driver.startswith("GATE:"):
        return None
    gate_name = driver.split(":", 1)[1]
    gate = design.gates.get(gate_name)
    if gate is None:
        return None
    visiting.add(net)
    values = [_eval_local_net(design, input_net, assignment, visiting) for input_net in gate.inputs]
    visiting.remove(net)
    if any(value is None for value in values):
        return None
    return _eval_primitive_gate(gate.type, [bool(value) for value in values])


def _eval_primitive_gate(gate_type: str, values: list[bool]) -> bool:
    if gate_type == "buf":
        return values[0]
    if gate_type == "not":
        return not values[0]
    if gate_type == "and":
        return all(values)
    if gate_type == "nand":
        return not all(values)
    if gate_type == "or":
        return any(values)
    if gate_type == "nor":
        return not any(values)
    if gate_type == "xor":
        return sum(1 for value in values if value) % 2 == 1
    if gate_type == "xnor":
        return sum(1 for value in values if value) % 2 == 0
    raise ValueError(f"Unsupported gate type: {gate_type}")


def _const_bool(net: str) -> bool | None:
    normalized = net.lower()
    if normalized in {"1'b1", "1"}:
        return True
    if normalized in {"1'b0", "0"}:
        return False
    return None


def _same_logic_ignoring_unused_wire_declarations(before: Any, after: Any) -> bool:
    if (
        before.module_name != after.module_name
        or set(before.inputs) != set(after.inputs)
        or set(before.outputs) != set(after.outputs)
        or set(before.gates) != set(after.gates)
        or set(before.dffs) != set(after.dffs)
        or not set(after.wires).issubset(before.wires)
    ):
        return False
    for name, gate in before.gates.items():
        candidate = after.gates[name]
        if (
            gate.type != candidate.type
            or gate.inputs != candidate.inputs
            or gate.output != candidate.output
        ):
            return False
    for name, dff in before.dffs.items():
        candidate = after.dffs[name]
        if (
            dff.d != candidate.d
            or dff.q != candidate.q
            or dff.clk != candidate.clk
            or dff.rst != candidate.rst
            or dff.set_signal != candidate.set_signal
            or dff.rst_value != candidate.rst_value
        ):
            return False
    referenced = set(before.inputs) | set(before.outputs)
    for gate in before.gates.values():
        referenced.add(gate.output)
        referenced.update(gate.inputs)
    for dff in before.dffs.values():
        referenced.update(
            net for net in (dff.d, dff.q, dff.clk, dff.rst, dff.set_signal)
            if net is not None
        )
    return not ((set(before.wires) - set(after.wires)) & referenced)


def _check_dedicated_buffer_identity(before: Any, after: Any, target: str) -> tuple[str, str]:
    if set(before.gates) - set(after.gates) or set(before.dffs) != set(after.dffs):
        return ("FAIL", "dedicated-buffer transform removed existing gates or changed DFF instances")
    added_names = set(after.gates) - set(before.gates)
    if not added_names:
        return ("FAIL", "dedicated-buffer transform added no BUF gates")
    added = [after.gates[name] for name in sorted(added_names)]
    if any(gate.type != "buf" or gate.inputs != [target] for gate in added):
        return ("FAIL", "an added gate is not a one-input BUF driven by the target net")
    outputs = [gate.output for gate in added]
    if len(set(outputs)) != len(outputs):
        return ("FAIL", "dedicated BUF outputs are not unique")

    output_map = {output: target for output in outputs}
    normalized_after = _designs_match_with_net_map_excluding_gates(after, before, output_map, added_names)
    if not normalized_after:
        return ("FAIL", "removing added BUF identities does not reconstruct the before design")

    rebuild_graph(before)
    rebuild_graph(after)
    original_sinks = list(before.fanouts.get(target, []))
    expected_buffered_sinks = sorted(sink for sink in original_sinks if not sink.startswith("PO:"))
    actual_buffered_sinks: list[str] = []
    for gate in added:
        sinks = after.fanouts.get(gate.output, [])
        if len(sinks) != 1:
            return ("FAIL", f'dedicated BUF "{gate.name}" drives {len(sinks)} load(s), expected 1')
        actual_buffered_sinks.append(sinks[0])
    if sorted(actual_buffered_sinks) != expected_buffered_sinks:
        return ("FAIL", "dedicated BUF sinks do not match the original target-net loads")
    return ("PASS", f"{len(added)} dedicated BUF identity insertion(s) reconstructed exactly")


def _designs_match_with_net_map_excluding_gates(
    transformed: Any,
    reference: Any,
    net_map: dict[str, str],
    excluded_gates: set[str],
) -> bool:
    def mapped(net: str | None) -> str | None:
        return net_map.get(net, net) if net is not None else None

    if transformed.module_name != reference.module_name:
        return False
    if {mapped(net) for net in transformed.inputs} != set(reference.inputs):
        return False
    if {mapped(net) for net in transformed.outputs} != set(reference.outputs):
        return False
    if {mapped(net) for net in transformed.wires} != set(reference.wires):
        return False
    if set(transformed.gates) - excluded_gates != set(reference.gates):
        return False
    for name, expected in reference.gates.items():
        candidate = transformed.gates[name]
        if (
            candidate.type != expected.type
            or [mapped(net) for net in candidate.inputs] != expected.inputs
            or mapped(candidate.output) != expected.output
            or candidate.attrs != expected.attrs
        ):
            return False
    for name, expected in reference.dffs.items():
        candidate = transformed.dffs[name]
        if (
            mapped(candidate.d) != expected.d
            or mapped(candidate.q) != expected.q
            or mapped(candidate.clk) != expected.clk
            or mapped(candidate.rst) != expected.rst
            or candidate.rst_value != expected.rst_value
            or candidate.attrs != expected.attrs
        ):
            return False
    return True


def _count_collapsible_inverter_pairs(design: Any) -> int:
    rebuild_graph(design)
    count = 0
    for second in design.gates.values():
        if second.type != "not" or len(second.inputs) != 1:
            continue
        mid_net = second.inputs[0]
        driver = design.drivers.get(mid_net)
        if not driver or not driver.startswith("GATE:"):
            continue
        first = design.gates.get(driver.split(":", 1)[1])
        if first is None or first.type != "not" or len(first.inputs) != 1:
            continue
        if design.fanouts.get(mid_net, []) == [f"GATE:{second.name}"]:
            count += 1
    return count


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


def _write_results_csv(path: Path, results: list[ValidationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(ValidationResult.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result.__dict__)


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


def _select_existing_ledger_cases(release_dir: Path, planner: str) -> list[str]:
    selected: set[str] = set()
    roots = [
        release_dir / "runner_output" / planner / "validation",
        release_dir / "output" / "validation",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_dir() and (path / "ledger.jsonl").exists():
                selected.add(path.name)
    return sorted(selected)


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


def _path_relevant_gate_count(design: Any, src: str, dst: str) -> int:
    adjacency = _validator_combinational_adjacency(design)
    reverse = _reverse_adjacency_map(adjacency)
    src_candidates = _resolve_validator_signal_candidates(design, src)
    dst_candidates = _resolve_validator_signal_candidates(design, dst)
    if not src_candidates or not dst_candidates:
        return 0
    forward: set[str] = set()
    for candidate in src_candidates:
        forward.update(_reachable_nodes_map(adjacency, candidate))
    backward: set[str] = set()
    for candidate in dst_candidates:
        backward.update(_reachable_nodes_map(reverse, candidate))
    relevant = forward & backward
    return sum(1 for name in getattr(design, "gates", {}) if name in relevant)


def _validator_combinational_adjacency(design: Any) -> dict[str, set[str]]:
    rebuild_graph(design)
    adjacency: dict[str, set[str]] = {}

    def add_edge(src: str, dst: str) -> None:
        adjacency.setdefault(src, set()).add(dst)

    for gate in getattr(design, "gates", {}).values():
        for input_net in gate.inputs:
            add_edge(input_net, gate.name)
        add_edge(gate.name, gate.output)
    for output in getattr(design, "outputs", set()):
        driver = getattr(design, "drivers", {}).get(output)
        if driver and driver.startswith("GATE:"):
            add_edge(driver.split(":", 1)[1], output)
        elif output in getattr(design, "inputs", set()):
            add_edge(output, output)
    return adjacency


def _reverse_adjacency_map(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for node, next_nodes in adjacency.items():
        reverse.setdefault(node, set())
        for nxt in next_nodes:
            reverse.setdefault(nxt, set()).add(node)
    return reverse


def _reachable_nodes_map(adjacency: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in adjacency.get(node, set()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _resolve_validator_signal_candidates(design: Any, name: str) -> list[str]:
    all_nets = design.all_nets()
    if name in all_nets:
        return [name]
    prefix = f"{name}["
    return sorted(net for net in all_nets if net.startswith(prefix))


def _fanout_equivalence_endpoints(before: Any, after: Any, target: str) -> list[str]:
    if not target:
        return []
    before_endpoints = _fanout_endpoints(before, target)
    after_endpoints = _fanout_endpoints(after, target)
    common_outputs = set(getattr(before, "outputs", set())) & set(getattr(after, "outputs", set()))
    common_dff_inputs = _common_dff_input_endpoints(before, after)
    allowed = common_outputs | common_dff_inputs
    return sorted((before_endpoints & after_endpoints) & allowed)


def _fanout_endpoints(design: Any, target: str) -> set[str]:
    rebuild_graph(design)
    starts = _resolve_validator_signal_candidates(design, target)
    endpoints: set[str] = set()
    seen = set(starts)
    stack = list(starts)
    while stack and len(endpoints) < VALIDATOR_LARGE_SELECTED_OUTPUT_LIMIT * 4:
        net = stack.pop()
        for sink in getattr(design, "fanouts", {}).get(net, []):
            kind, name = sink.split(":", 1)
            if kind == "PO":
                endpoints.add(name)
            elif kind == "DFF":
                dff = design.dffs.get(name)
                if dff is not None and dff.d == net:
                    endpoints.add(net)
            elif kind == "GATE":
                gate = design.gates.get(name)
                if gate is not None and gate.output not in seen:
                    seen.add(gate.output)
                    stack.append(gate.output)
    return endpoints


def _common_dff_input_endpoints(before: Any, after: Any) -> set[str]:
    before_inputs = {dff.d for dff in getattr(before, "dffs", {}).values()}
    after_inputs = {dff.d for dff in getattr(after, "dffs", {}).values()}
    return before_inputs & after_inputs


def _is_common_dff_input_endpoint(before: Any, after: Any, net: str) -> bool:
    return net in _common_dff_input_endpoints(before, after)


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
