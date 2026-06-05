from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.state import CurrentState
from parser.verilog_parser import parse_verilog
from parser.verilog_writer import write_verilog
from eda.analysis import (
    all_paths_pass_through,
    direct_fanout,
    find_gates,
    find_path,
    gate_connections,
    gate_counts,
    logic_cone,
    max_depth,
    primary_output_cone_sizes,
)
from eda.transform import (
    balance_depth_with_buffers,
    insert_buffers_for_fanout,
    optimize_cone,
    remove_dangling,
    rename_net,
    replace_buffers_with_and,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
)
from eda.verify import (
    check_connectivity,
    check_depth,
    check_design_equivalence,
    check_equivalence,
    check_fanout,
    check_property,
)

SUPPORTED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_gates",
    "find_path",
    "all_paths_pass_through",
    "max_depth",
    "logic_cone",
    "report_gate_counts",
    "report_fanout",
    "report_gate_connections",
    "report_outputs_by_cone_size",
    "same_clock_domain",
    "replace_buffers_with_and",
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "replace_or_with_nand_not",
    "insert_buffers_for_fanout",
    "balance_depth_with_buffers",
    "optimize_cone",
    "rename_net",
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalent_to_original",
    "check_equivalence",
    "check_property",
    "unsupported",
}

REQUIRED_ARGS = {
    "begin_testcase": ("case_name",),
    "read_design": ("path",),
    "write_design": ("path",),
    "find_gates": (),
    "find_path": ("src", "dst"),
    "all_paths_pass_through": ("src", "dst", "node"),
    "max_depth": ("src", "dst"),
    "logic_cone": ("target",),
    "report_gate_counts": (),
    "report_fanout": ("net",),
    "report_gate_connections": ("gate",),
    "report_outputs_by_cone_size": ("min_gates",),
    "same_clock_domain": ("dff_a", "dff_b"),
    "replace_buffers_with_and": ("extra_input",),
    "remove_dangling": (),
    "replace_inv_buf_with_inv": (),
    "replace_or_with_nand_not": ("cone_target",),
    "insert_buffers_for_fanout": ("net", "max_fanout"),
    "balance_depth_with_buffers": ("src", "dsts"),
    "optimize_cone": ("target",),
    "rename_net": ("old_net", "new_net"),
    "check_connectivity": (),
    "check_fanout": ("max_fanout",),
    "check_depth": ("src", "dst", "max_depth"),
    "check_equivalent_to_original": (),
    "check_equivalence": ("expr", "target"),
    "check_property": ("target", "property"),
    "unsupported": ("reason",),
}


def dispatch_plan(state: CurrentState, plan: dict[str, Any]) -> str:
    """Execute a single tool call or a multi-step plan."""
    if not isinstance(plan, dict):
        raise ValueError("Tool call rejected: plan must be a dictionary.")

    if "steps" in plan:
        steps = plan["steps"]
        if not isinstance(steps, list):
            raise ValueError("Tool call rejected: steps must be a list.")
        messages = []
        for index, step in enumerate(steps, 1):
            try:
                messages.append(dispatch_plan(state, step))
            except Exception as exc:
                raise RuntimeError(f"Step {index} failed: {exc}") from exc
        return "\n".join(messages)

    op = plan.get("op")
    args = plan.get("args", {}) or {}
    save_as = plan.get("save_as")
    _validate_tool_call(op, args)

    if op == "unsupported":
        return (
            "I could not map the request to a supported EDA operation. "
            f"Reason: {args['reason']}"
        )

    if op == "begin_testcase":
        case_name = args["case_name"]
        state.begin_testcase(case_name)
        return (
            f'Acknowledged. Initialized testcase "{case_name}". '
            f"All subsequent responses will be recorded to {state.log_path}. "
            "Design state is empty and ready for commands."
        )

    if op == "read_design":
        path = _resolve_read_path(args["path"])
        state.design = parse_verilog(path)
        state.original_design = deepcopy(state.design)
        state.design_path = path
        return f'Loaded gate-level Verilog from "{path}" successfully.\n- {state.design.summary()}'

    if op == "write_design":
        _require_design(state)
        path = _resolve_write_path(args["path"])
        write_verilog(state.design, path)
        return f'Wrote the modified netlist to "{path}" successfully.'

    if op == "find_gates":
        _require_design(state)
        result = find_gates(
            state.design,
            gate_type=args.get("gate_type"),
            name_contains=args.get("name_contains"),
        )
        if save_as:
            state.remember_result(save_as, result, kind="gate_list")
        return _format_list_result("Matched gates", result)

    if op == "find_path":
        _require_design(state)
        path = find_path(
            state.design,
            src=args["src"],
            dst=args["dst"],
            avoid=args.get("avoid", []),
        )
        if not path:
            return f'No path found from "{args["src"]}" to "{args["dst"]}".'
        return "Found path:\n" + " -> ".join(path)

    if op == "all_paths_pass_through":
        _require_design(state)
        ok = all_paths_pass_through(
            state.design,
            src=args["src"],
            dst=args["dst"],
            node=args["node"],
        )
        if ok:
            return (
                f'Yes. Every combinational path from "{args["src"]}" '
                f'to "{args["dst"]}" passes through "{args["node"]}".'
            )
        avoiding_path = find_path(
            state.design,
            src=args["src"],
            dst=args["dst"],
            avoid=[args["node"]],
        )
        if avoiding_path:
            return (
                f'No. There is a combinational path from "{args["src"]}" '
                f'to "{args["dst"]}" that avoids "{args["node"]}":\n'
                + " -> ".join(avoiding_path)
            )
        return (
            f'No. No combinational path from "{args["src"]}" '
            f'to "{args["dst"]}" was found.'
        )

    if op == "max_depth":
        _require_design(state)
        depth, path = max_depth(state.design, args["src"], args["dst"])
        return (
            f'The maximum logic depth from "{args["src"]}" to "{args["dst"]}" is {depth}.\n'
            f'Example path: {" -> ".join(path) if path else "(none)"}'
        )

    if op == "logic_cone":
        _require_design(state)
        gates = logic_cone(state.design, args["target"])
        if save_as:
            state.remember_result(save_as, gates, kind="gate_list")
        return f'Logic cone of "{args["target"]}" contains {len(gates)} gates:\n' + "\n".join(gates)

    if op == "report_gate_counts":
        _require_design(state)
        return _format_gate_counts(gate_counts(state.design))

    if op == "report_fanout":
        _require_design(state)
        return _format_fanout(direct_fanout(state.design, args["net"]))

    if op == "report_gate_connections":
        _require_design(state)
        return _format_gate_connections(gate_connections(state.design, args["gate"]))

    if op == "report_outputs_by_cone_size":
        _require_design(state)
        min_gates = args["min_gates"]
        report = primary_output_cone_sizes(state.design)
        matched = [
            (output, item["num_gates"])
            for output, item in sorted(report.items())
            if item["num_gates"] > min_gates
        ]
        if not matched:
            return f"No primary outputs have logic cones with more than {min_gates} gates."
        lines = [f"Primary outputs with logic cones larger than {min_gates} gates:"]
        for output, num_gates in matched:
            lines.append(f"- {output}: {num_gates} gates")
        return "\n".join(lines)

    if op == "same_clock_domain":
        _require_design(state)
        dff_a = state.design.dffs.get(args["dff_a"])
        dff_b = state.design.dffs.get(args["dff_b"])
        if dff_a is None:
            raise ValueError(f'DFF not found: {args["dff_a"]}')
        if dff_b is None:
            raise ValueError(f'DFF not found: {args["dff_b"]}')
        if dff_a.clk == dff_b.clk:
            return (
                f'Yes. "{args["dff_a"]}" and "{args["dff_b"]}" '
                f'are in the same clock domain "{dff_a.clk}".'
            )
        return (
            f'No. "{args["dff_a"]}" uses clock "{dff_a.clk}", '
            f'while "{args["dff_b"]}" uses clock "{dff_b.clk}".'
        )

    if op == "replace_buffers_with_and":
        _require_design(state)
        targets = args.get("targets")
        if targets is None and "targets_from" in args:
            targets = state.get_result(args["targets_from"], expected_kind="gate_list")
        if targets is None:
            raise ValueError('Tool call rejected: "targets" or "targets_from" is required.')
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise ValueError('Tool call rejected: "targets" must be a list of gate names.')
        result = _run_transactional_transform(
            state,
            replace_buffers_with_and,
            targets=targets,
            extra_input=args["extra_input"],
        )
        return f'Replaced {result["num_changed"]} buffer(s) with AND gate(s): {result["changed"]}'

    if op == "remove_dangling":
        _require_design(state)
        result = _run_transactional_transform(state, remove_dangling, verify_equivalence=True)
        return (
            "Removed dangling logic: "
            f'{result["num_removed_gates"]} gate(s), '
            f'{result["num_removed_dffs"]} DFF(s), '
            f'{result["num_removed_nets"]} net(s).\n'
            f'Gates: {result["removed_gates"]}\n'
            f'Nets: {result["removed_nets"]}'
        )

    if op == "replace_inv_buf_with_inv":
        _require_design(state)
        result = _run_transactional_transform(state, replace_inv_buf_with_inv, verify_equivalence=True)
        return f'Replaced {result["num_changed"]} inverter-buffer chain(s): {result["changed"]}'

    if op == "replace_or_with_nand_not":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            replace_or_with_nand_not,
            args["cone_target"],
            verify_equivalence=True,
        )
        return (
            f'Replaced {result["num_changed"]} OR gate(s) in the cone of '
            f'"{args["cone_target"]}" with NAND/NOT logic: {result["changed"]}'
        )

    if op == "insert_buffers_for_fanout":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            insert_buffers_for_fanout,
            args["net"],
            args["max_fanout"],
            verify_equivalence=True,
            max_fanout=args["max_fanout"],
        )
        return (
            f'Inserted {result["num_inserted_buffers"]} buffer(s) on net '
            f'"{args["net"]}". Final max fanout is {result["final_max_fanout"]}.'
        )

    if op == "balance_depth_with_buffers":
        _require_design(state)
        dsts = args["dsts"]
        if not isinstance(dsts, list) or not all(isinstance(item, str) for item in dsts):
            raise ValueError('Tool call rejected: "dsts" must be a list of destination net names.')
        result = _run_transactional_transform(
            state,
            balance_depth_with_buffers,
            args["src"],
            dsts,
            minimize_buffers=args.get("minimize_buffers", True),
            verify_equivalence=True,
            depth_balance=(args["src"], dsts),
        )
        return (
            f'Inserted {result["num_inserted_buffers"]} buffer(s) to balance depths from '
            f'"{args["src"]}". Final depths: {result["final_depths"]}.'
        )

    if op == "optimize_cone":
        _require_design(state)
        max_allowed_depth = args.get("max_depth")
        if max_allowed_depth is not None and not isinstance(max_allowed_depth, int):
            raise ValueError('Tool call rejected: "max_depth" must be an integer when provided.')
        result = _run_transactional_transform(
            state,
            optimize_cone,
            args["target"],
            max_depth=max_allowed_depth,
            minimize_gate_count=args.get("minimize_gate_count", True),
            verify_equivalence=True,
            cone_depth=(args["target"], max_allowed_depth),
        )
        return (
            f'Optimized cone of "{args["target"]}": '
            f'{result["initial_gate_count"]} -> {result["final_gate_count"]} gate(s), '
            f'depth {result["initial_depth"]} -> {result["final_depth"]}.'
        )

    if op == "rename_net":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            rename_net,
            args["old_net"],
            args["new_net"],
            verify_equivalence=True,
        )
        return (
            f'Renamed net "{result["old_net"]}" to "{result["new_net"]}" '
            f'across {result["num_references"]} reference(s).'
        )

    if op == "check_connectivity":
        _require_design(state)
        return str(check_connectivity(state.design))

    if op == "check_fanout":
        _require_design(state)
        return str(check_fanout(state.design, args["max_fanout"]))

    if op == "check_depth":
        _require_design(state)
        return str(check_depth(state.design, args["src"], args["dst"], args["max_depth"]))

    if op == "check_equivalent_to_original":
        _require_design(state)
        if state.original_design is None:
            raise RuntimeError("No original design snapshot is available. Load a design with read_design first.")
        result = check_design_equivalence(state.original_design, state.design)
        return _format_original_equivalence_result(result)

    if op == "check_equivalence":
        _require_design(state)
        result = check_equivalence(state.design, args["expr"], args["target"])
        return _format_equivalence_result(args["expr"], args["target"], result)

    if op == "check_property":
        _require_design(state)
        result = check_property(state.design, args["target"], args["property"])
        return _format_property_result(args["target"], args["property"], result)

    raise ValueError(f"Unsupported operation: {op}")


def _validate_tool_call(op: Any, args: Any) -> None:
    """Reject unsupported operations or missing required arguments."""
    if not isinstance(op, str) or not op:
        raise ValueError("Tool call rejected: missing operation name.")
    if op not in SUPPORTED_OPS:
        raise ValueError(f"Unsupported operation: {op}")
    if not isinstance(args, dict):
        raise ValueError(f'Tool call rejected: args for "{op}" must be a dictionary.')
    missing = [name for name in REQUIRED_ARGS[op] if name not in args]
    if missing:
        raise ValueError(
            f'Tool call rejected: "{op}" missing required argument(s): '
            + ", ".join(missing)
        )


def _require_design(state: CurrentState) -> None:
    """Ensure a design has been loaded before running design operations."""
    if state.design is None:
        raise RuntimeError("No design has been loaded yet.")


def _run_transactional_transform(
    state: CurrentState,
    transform,
    *args: Any,
    verify_equivalence: bool = False,
    max_fanout: int | None = None,
    depth_balance: tuple[str, list[str]] | None = None,
    cone_depth: tuple[str, int | None] | None = None,
    **kwargs: Any,
) -> dict:
    """
    Run a transform on a copied design and commit only after verification.

    This keeps a failed or structurally invalid transform from polluting the
    testcase's evolving design state.
    """
    _require_design(state)
    original = state.design
    candidate = deepcopy(original)
    result = transform(candidate, *args, **kwargs)
    connectivity = check_connectivity(candidate)
    if not connectivity.get("ok", False):
        raise RuntimeError(f"Transformation rejected: connectivity check failed: {connectivity}")
    if verify_equivalence:
        equivalence = check_design_equivalence(original, candidate)
        if not equivalence.get("ok", False):
            raise RuntimeError(f"Transformation rejected: equivalence check failed: {equivalence}")
    if max_fanout is not None:
        fanout = check_fanout(candidate, max_fanout)
        if not fanout.get("ok", False):
            raise RuntimeError(f"Transformation rejected: fanout check failed: {fanout}")
    if depth_balance is not None:
        src, dsts = depth_balance
        balance = _check_depth_balance(candidate, src, dsts)
        if not balance.get("ok", False):
            raise RuntimeError(f"Transformation rejected: depth balance check failed: {balance}")
    if cone_depth is not None:
        target, max_allowed_depth = cone_depth
        depth = _check_cone_depth_bound(candidate, target, max_allowed_depth)
        if not depth.get("ok", False):
            raise RuntimeError(f"Transformation rejected: cone depth check failed: {depth}")
    state.design = candidate
    return result


def _resolve_read_path(path: str) -> Path:
    """Validate and return the input Verilog path."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Design file not found: {path}")
    if p.suffix != ".v":
        raise ValueError(f"Design file must be a .v file: {path}")
    return p


def _resolve_write_path(path: str) -> Path:
    """Validate and return the output Verilog path."""
    p = Path(path)
    if p.suffix != ".v":
        raise ValueError(f"Output design file must be a .v file: {path}")
    return p


def _format_list_result(title: str, items: list[str]) -> str:
    """Format a list result as numbered response text."""
    if not items:
        return f"{title}: none."
    lines = [f"{title}: {len(items)}"]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item}")
    return "\n".join(lines)


def _format_gate_counts(result: dict[str, Any]) -> str:
    lines = ["Gate counts:"]
    for gate_type, count in result["counts"].items():
        lines.append(f"- {gate_type}: {count}")
    lines.append(f'Total gates: {result["total"]}')
    return "\n".join(lines)


def _format_fanout(result: dict[str, Any]) -> str:
    lines = [
        f'Fanout of {result["source_kind"]} "{result["source"]}" '
        f'(net "{result["net"]}"): '
        f'{result["num_loads"]} load(s), {result["num_unique_sinks"]} unique sink(s), '
        f'{result["num_gate_sinks"]} driven gate(s).'
    ]
    for sink in result["sinks"]:
        if sink["kind"] == "gate":
            pins = ", ".join(str(pin) for pin in sink["input_pins"]) or "(none)"
            lines.append(
                f'- gate {sink["name"]} ({sink["gate_type"]}), input pin(s) {pins}, output {sink["output"]}'
            )
        elif sink["kind"] == "dff":
            pins = ", ".join(sink["pins"]) or "(none)"
            lines.append(f'- DFF {sink["name"]}, pin(s) {pins}, Q {sink["output"]}')
        elif sink["kind"] == "primary_output":
            lines.append(f'- primary output {sink["name"]}')
        else:
            lines.append(f'- {sink["sink"]}')
    return "\n".join(lines)


def _format_gate_connections(result: dict[str, Any]) -> str:
    if result["kind"] == "gate":
        inputs = ", ".join(result["inputs"]) or "(none)"
        lines = [
            f'Gate "{result["instance"]}": type={result["gate_type"]}, inputs=[{inputs}], output={result["output"]}.',
            "Output fanout:",
        ]
    else:
        pins = ", ".join(
            f"{pin}={net}" for pin, net in result["pins"].items() if net is not None
        )
        lines = [
            f'DFF "{result["instance"]}": {pins}.',
            "Q fanout:",
        ]
    if not result["output_fanout"]:
        lines.append("- none")
    else:
        for sink in result["output_fanout"]:
            lines.append(f'- {sink["sink"]}')
    return "\n".join(lines)


def _format_equivalence_result(expr: str, target: str, result: dict[str, Any]) -> str:
    if result.get("ok"):
        return (
            f'Equivalent. Expression "{expr}" matches target "{target}" '
            f'for all checked assignments using {result.get("engine")} engine.'
        )
    lines = [
        f'Not equivalent. Expression "{expr}" does not match target "{target}".',
        _format_counterexample(result),
    ]
    if result.get("reason"):
        lines.append(f'Reason: {result["reason"]}')
    return "\n".join(line for line in lines if line)


def _format_property_result(target: str, property_text: str, result: dict[str, Any]) -> str:
    if result.get("ok"):
        return (
            f'Property holds for "{target}": {property_text}. '
            f'Checked with {result.get("engine")} engine.'
        )
    lines = [
        f'Property does not hold for "{target}": {property_text}.',
        _format_counterexample(result),
    ]
    if result.get("reason"):
        lines.append(f'Reason: {result["reason"]}')
    return "\n".join(line for line in lines if line)


def _format_original_equivalence_result(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return (
            "Equivalent to original loaded netlist. "
            f'Checked with {result.get("engine")} engine over combinational boundaries.'
        )
    lines = [
        "Not equivalent to original loaded netlist.",
        _format_counterexample(result),
    ]
    if result.get("reason"):
        lines.append(f'Reason: {result["reason"]}')
    return "\n".join(line for line in lines if line)


def _format_counterexample(result: dict[str, Any]) -> str:
    counterexample = result.get("counterexample")
    if not isinstance(counterexample, dict):
        return ""
    assignments = ", ".join(
        f"{name}={int(value)}"
        for name, value in sorted(counterexample.items())
    )
    return f"Counterexample: {assignments}."


def _check_depth_balance(design, src: str, dsts: list[str]) -> dict[str, Any]:
    depths: dict[str, int] = {}
    for dst in dsts:
        depth, path = max_depth(design, src, dst)
        if not path:
            return {"ok": False, "reason": f'No path from "{src}" to "{dst}".', "depths": depths}
        depths[dst] = depth
    unique_depths = set(depths.values())
    return {"ok": len(unique_depths) <= 1, "depths": depths}


def _check_cone_depth_bound(design, target: str, max_allowed_depth: int | None) -> dict[str, Any]:
    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    depths: dict[str, int] = {}
    for source in sorted(sources):
        depth, path = max_depth(design, source, target)
        if path:
            depths[source] = depth
    final_depth = max(depths.values(), default=0)
    if max_allowed_depth is None:
        return {"ok": True, "target": target, "final_depth": final_depth, "source_depths": depths}
    return {
        "ok": final_depth <= max_allowed_depth,
        "target": target,
        "final_depth": final_depth,
        "max_allowed_depth": max_allowed_depth,
        "source_depths": depths,
    }
