from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.state import CurrentState
from parser.verilog_parser import parse_verilog
from parser.verilog_writer import write_verilog
from eda.analysis import (
    all_paths,
    all_paths_pass_through,
    articulation_points_between,
    constant_input_gates,
    dff_relationships,
    dffs_by_clock,
    direct_pi_po_paths,
    direct_fanout,
    derive_boolean_equation,
    fanout_cone,
    find_gates,
    find_path,
    gate_on_max_depth_path,
    gate_connections,
    gate_counts,
    gate_type_count,
    gate_type_connections,
    highest_fanout_primary_input,
    io_counts,
    logic_cone,
    max_depth_to_dff_d,
    max_depth,
    outputs_depth_greater_than,
    primary_output_cone_sizes,
    register_to_register_paths,
    shared_fanin_cone_gates,
)
from eda.transform import (
    balance_depth_with_buffers,
    collapse_back_to_back_inverters,
    constant_propagation,
    insert_dedicated_buffers_for_each_load,
    insert_buffers_for_all_high_fanout,
    insert_buffers_for_fanout,
    merge_equivalent_gates,
    optimize_cone,
    optimize_design_depth,
    replace_nand_const1_with_not,
    remove_dangling,
    rename_gate,
    rename_net,
    replace_and_not_with_nand,
    replace_buffers_with_and,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
    replace_xnor_nor_with_basic_gates,
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
    "report_all_paths",
    "max_depth",
    "logic_cone",
    "report_gate_counts",
    "report_gate_type_count",
    "report_gate_type_connections",
    "report_direct_pi_po_paths",
    "report_dffs_by_clock",
    "report_fanout",
    "report_highest_fanout_primary_input",
    "report_gate_connections",
    "report_outputs_by_cone_size",
    "report_fanout_cone",
    "report_constant_input_gates",
    "report_io_counts",
    "gate_on_max_depth_path",
    "report_articulation_points",
    "report_shared_fanin_cone_gates",
    "derive_boolean_equation",
    "report_max_depth_to_dff_d",
    "report_outputs_depth_greater_than",
    "report_register_paths",
    "report_last_transform_stats",
    "same_clock_domain",
    "replace_buffers_with_and",
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "collapse_back_to_back_inverters",
    "replace_or_with_nand_not",
    "replace_nand_const1_with_not",
    "insert_buffers_for_fanout",
    "insert_dedicated_buffers_for_each_load",
    "insert_buffers_for_all_high_fanout",
    "balance_depth_with_buffers",
    "optimize_cone",
    "constant_propagation",
    "optimize_design_depth",
    "replace_xnor_nor_with_basic_gates",
    "replace_and_not_with_nand",
    "merge_equivalent_gates",
    "rename_gate",
    "rename_net",
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalent_to_original",
    "check_equivalent_to_last_transform_input",
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
    "report_all_paths": ("src", "dst"),
    "max_depth": ("src", "dst"),
    "logic_cone": ("target",),
    "report_gate_counts": (),
    "report_gate_type_count": ("gate_type",),
    "report_gate_type_connections": ("gate_type",),
    "report_direct_pi_po_paths": (),
    "report_dffs_by_clock": ("clock",),
    "report_fanout": ("net",),
    "report_highest_fanout_primary_input": (),
    "report_gate_connections": ("gate",),
    "report_outputs_by_cone_size": ("min_gates",),
    "report_fanout_cone": ("source",),
    "report_constant_input_gates": (),
    "report_io_counts": (),
    "gate_on_max_depth_path": ("gate",),
    "report_articulation_points": ("src", "dst"),
    "report_shared_fanin_cone_gates": ("target_a", "target_b"),
    "derive_boolean_equation": ("target",),
    "report_max_depth_to_dff_d": (),
    "report_outputs_depth_greater_than": ("min_depth",),
    "report_register_paths": (),
    "report_last_transform_stats": (),
    "same_clock_domain": ("dff_a", "dff_b"),
    "replace_buffers_with_and": ("extra_input",),
    "remove_dangling": (),
    "replace_inv_buf_with_inv": (),
    "collapse_back_to_back_inverters": (),
    "replace_or_with_nand_not": ("cone_target",),
    "replace_nand_const1_with_not": (),
    "insert_buffers_for_fanout": ("net", "max_fanout"),
    "insert_dedicated_buffers_for_each_load": ("net",),
    "insert_buffers_for_all_high_fanout": ("max_fanout",),
    "balance_depth_with_buffers": ("src", "dsts"),
    "optimize_cone": ("target",),
    "constant_propagation": (),
    "optimize_design_depth": (),
    "replace_xnor_nor_with_basic_gates": (),
    "replace_and_not_with_nand": (),
    "merge_equivalent_gates": (),
    "rename_gate": ("old_name", "new_name"),
    "rename_net": ("old_net", "new_net"),
    "check_connectivity": (),
    "check_fanout": ("max_fanout",),
    "check_depth": ("src", "dst", "max_depth"),
    "check_equivalent_to_original": (),
    "check_equivalent_to_last_transform_input": (),
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

    if op == "report_all_paths":
        _require_design(state)
        return _format_all_paths(
            all_paths(
                state.design,
                src=args["src"],
                dst=args["dst"],
                max_paths=_positive_int_or_default(args.get("max_paths"), 200),
            )
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
    if op == "report_gate_type_count":
        _require_design(state)
        return _format_gate_type_count(gate_type_count(state.design, args["gate_type"]))

    if op == "report_gate_type_connections":
        _require_design(state)
        return _format_gate_type_connections(
            gate_type_connections(
                state.design,
                args["gate_type"],
                max_items=_positive_int_or_default(args.get("max_items"), 200),
            )
        )

    if op == "report_direct_pi_po_paths":
        _require_design(state)
        return _format_direct_pi_po_paths(direct_pi_po_paths(state.design))

    if op == "report_dffs_by_clock":
        _require_design(state)
        return _format_dffs_by_clock(
            dffs_by_clock(
                state.design,
                args["clock"],
                max_items=_positive_int_or_default(args.get("max_items"), 200),
            )
        )
    if op == "report_fanout":
        _require_design(state)
        return _format_fanout(direct_fanout(state.design, args["net"]))

    if op == "report_highest_fanout_primary_input":
        _require_design(state)
        return _format_highest_fanout_primary_input(highest_fanout_primary_input(state.design))

    if op == "report_fanout_cone":
        _require_design(state)
        return _format_fanout_cone(fanout_cone(state.design, args["source"]))

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

    if op == "report_constant_input_gates":
        _require_design(state)
        return _format_constant_input_gates(
            constant_input_gates(state.design, gate_type=args.get("gate_type"))
        )

    if op == "report_io_counts":
        _require_design(state)
        result = io_counts(state.design)
        return (
            "Primary IO counts:\n"
            f'- inputs: {result["num_inputs"]}\n'
            f'- outputs: {result["num_outputs"]}'
        )

    if op == "gate_on_max_depth_path":
        _require_design(state)
        result = gate_on_max_depth_path(state.design, args["gate"])
        answer = "Yes" if result["on_max_depth_path"] else "No"
        lines = [
            f'{answer}. Gate "{args["gate"]}" '
            f'{"lies" if result["on_max_depth_path"] else "does not lie"} '
            "on a maximum-depth combinational path.",
            f'Global maximum depth: {result["global_max_depth"]}.',
        ]
        if result.get("gate_path_depth") is not None:
            lines.append(f'Best path through gate depth: {result["gate_path_depth"]}.')
        if result.get("example_prefix_path"):
            lines.append("Example prefix: " + " -> ".join(result["example_prefix_path"]))
        if result.get("reason"):
            lines.append(f'Reason: {result["reason"]}')
        return "\n".join(lines)

    if op == "report_articulation_points":
        _require_design(state)
        return _format_articulation_points(
            articulation_points_between(state.design, args["src"], args["dst"])
        )

    if op == "report_shared_fanin_cone_gates":
        _require_design(state)
        return _format_shared_fanin_cone_gates(
            shared_fanin_cone_gates(state.design, args["target_a"], args["target_b"])
        )

    if op == "derive_boolean_equation":
        _require_design(state)
        result = derive_boolean_equation(state.design, args["target"])
        suffix = " (truncated)" if result["truncated"] else ""
        return f'Boolean equation for "{args["target"]}"{suffix}: {args["target"]} = {result["expression"]}'

    if op == "report_max_depth_to_dff_d":
        _require_design(state)
        return _format_max_depth_to_dff_d(max_depth_to_dff_d(state.design))

    if op == "report_outputs_depth_greater_than":
        _require_design(state)
        return _format_outputs_depth_greater_than(outputs_depth_greater_than(state.design, args["min_depth"]))

    if op == "report_register_paths":
        _require_design(state)
        result = register_to_register_paths(state.design, max_paths=args.get("max_paths", 200))
        lines = [f'Register-to-register combinational paths: {result["num_paths"]}']
        if result["truncated"]:
            lines.append(f'Showing first {result["max_paths"]} path(s).')
        for item in result["paths"]:
            lines.append(
                f'- {item["src_dff"]} -> {item["dst_dff"]}: '
                + " -> ".join(item["path"])
            )
        if not result["paths"]:
            lines.append("- none")
        return "\n".join(lines)

    if op == "report_last_transform_stats":
        return _format_last_transform_stats(state.last_transform_result)

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
        return (
            f'Replaced {result["num_changed"]} inverter-buffer chain(s). '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "collapse_back_to_back_inverters":
        _require_design(state)
        result = _run_transactional_transform(state, collapse_back_to_back_inverters, verify_equivalence=True)
        return (
            f'Collapsed {result["num_changed"]} back-to-back inverter pair(s). '
            f'{_format_change_sample(result["changed"])}'
        )

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
            f'"{args["cone_target"]}" with NAND/NOT logic. '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "replace_nand_const1_with_not":
        _require_design(state)
        result = _run_transactional_transform(state, replace_nand_const1_with_not)
        return (
            f'Replaced {result["num_changed"]} 2-input NAND gate(s) with one '
            f'constant-1 input by inverter(s). '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "insert_buffers_for_fanout":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            insert_buffers_for_fanout,
            args["net"],
            args["max_fanout"],
            # Buffer-tree insertion is a structural identity; avoid expensive
            # full-design formal checks on large release netlists.
            verify_equivalence=False,
            max_fanout=args["max_fanout"],
            fanout_bound_net=args["net"],
        )
        return (
            f'Inserted {result["num_inserted_buffers"]} buffer(s) on net '
            f'"{args["net"]}". Final fanout of "{args["net"]}" is '
            f'{result["final_net_fanout"]}.'
        )

    if op == "insert_dedicated_buffers_for_each_load":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            insert_dedicated_buffers_for_each_load,
            args["net"],
            verify_equivalence=False,
        )
        return (
            f'Inserted {result["num_inserted_buffers"]} dedicated buffer(s) on signal '
            f'"{args["net"]}". Final direct loads on original signal: '
            f'{result["final_direct_loads"]}. Skipped sinks: {result["skipped_sinks"]}'
        )

    if op == "insert_buffers_for_all_high_fanout":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            insert_buffers_for_all_high_fanout,
            args["max_fanout"],
            max_changed_nets=64 if len(state.design.gates) > 10000 else None,
            verify_equivalence=False,
            max_fanout=args["max_fanout"],
        )
        return (
            f'Inserted {result["num_inserted_buffers"]} buffer(s) across '
            f'{result["num_changed_nets"]} high-fanout net(s). '
            f'Final max fanout is {result["final_max_fanout"]}. '
            f'{_format_change_sample(result["skipped"], label="Skipped")}'
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
        if len(state.design.gates) > 10000 and max_allowed_depth is not None:
            return (
                f'Skipped cone optimization of "{args["target"]}" to stay within '
                "the 60-second per-response limit. No structural changes were applied."
            )
        result = _run_transactional_transform(
            state,
            optimize_cone,
            args["target"],
            max_depth=max_allowed_depth,
            minimize_gate_count=args.get("minimize_gate_count", True),
            verify_equivalence=True,
            cone_depth=(args["target"], max_allowed_depth),
        )
        resolved_text = ""
        if result.get("resolved_target") and result["resolved_target"] != args["target"]:
            resolved_text = f' Resolved target to "{result["resolved_target"]}" ({result["target_resolution"]["kind"]}).'
        return (
            f'Optimized cone of "{args["target"]}": '
            f'{result["initial_gate_count"]} -> {result["final_gate_count"]} gate(s), '
            f'depth {result["initial_depth"]} -> {result["final_depth"]}.'
            f'{resolved_text}'
        )

    if op == "constant_propagation":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            constant_propagation,
            max_changes=64 if len(state.design.gates) > 10000 else None,
            verify_equivalence=False if len(state.design.gates) > 10000 else True,
        )
        return (
            f'Propagated constants through {result["num_changed"]} gate(s). '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "optimize_design_depth":
        _require_design(state)
        max_allowed_depth = args.get("max_depth")
        if max_allowed_depth is not None and not isinstance(max_allowed_depth, int):
            raise ValueError('Tool call rejected: "max_depth" must be an integer when provided.')
        result = _run_transactional_transform(
            state,
            optimize_design_depth,
            max_depth=max_allowed_depth,
            max_outputs=16 if len(state.design.gates) > 10000 else None,
            verify_equivalence=False,
        )
        engine = result.get("engine", "unknown")
        target_text = ""
        if max_allowed_depth is not None:
            target_text = f' Target depth <= {max_allowed_depth}: {"met" if result.get("target_met") else "not met"}.'
        fallback_text = ""
        if result.get("fallback_reason"):
            fallback_text = " Yosys/ABC candidate was not applied because it did not pass safety checks; kept the safe result."
        if result.get("initial_depth") is None or result.get("final_depth") is None:
            depth_text = "depth not recomputed in bounded mode"
        else:
            depth_text = f'depth {result["initial_depth"]} -> {result["final_depth"]}'
        bounded_text = f' {result["bounded_reason"]}.' if result.get("bounded_reason") else ""
        return (
            f'Optimized design depth with {engine}: gates {result["initial_gate_count"]} -> '
            f'{result["final_gate_count"]}, {depth_text}, changed targets {result["num_changed_outputs"]}.'
            f'{target_text}{fallback_text}{bounded_text}'
        )

    if op == "replace_xnor_nor_with_basic_gates":
        _require_design(state)
        if len(state.design.gates) > 10000:
            return "Skipped full-design XNOR/NOR remap for this large design to stay within the 60-second per-response limit. No structural changes were applied."
        result = _run_transactional_transform(state, replace_xnor_nor_with_basic_gates)
        return (
            f'Remapped {result["num_changed"]} XNOR/NOR gate(s). '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "replace_and_not_with_nand":
        _require_design(state)
        if len(state.design.gates) > 10000:
            return "Skipped full-design AND/NOT-to-NAND remap for this large design to stay within the 60-second per-response limit. No structural changes were applied."
        result = _run_transactional_transform(state, replace_and_not_with_nand)
        return (
            f'Remapped {result["num_changed"]} AND/NOT gate(s) into NAND logic. '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "merge_equivalent_gates":
        _require_design(state)
        result = _run_transactional_transform(state, merge_equivalent_gates, verify_equivalence=True)
        return (
            f'Merged {result["num_merged"]} structurally equivalent gate(s). '
            f'{_format_change_sample(result["changed"])}'
        )

    if op == "rename_gate":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            rename_gate,
            args["old_name"],
            args["new_name"],
            verify_equivalence=False,
        )
        return (
            f'Renamed {result["kind"]} instance "{result["old_name"]}" '
            f'to "{result["new_name"]}" without changing connectivity.'
        )
    if op == "rename_net":
        _require_design(state)
        result = _run_transactional_transform(
            state,
            rename_net,
            args["old_net"],
            args["new_net"],
            verify_equivalence=False,
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

    if op == "check_equivalent_to_last_transform_input":
        _require_design(state)
        if state.last_transform_input is None:
            raise RuntimeError("No previous transform input snapshot is available. Run a transform first.")
        result = check_design_equivalence(state.last_transform_input, state.design)
        return _format_last_transform_equivalence_result(result)

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
    fanout_bound_net: str | None = None,
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
    original_connectivity = check_connectivity(original)
    candidate = deepcopy(original)
    result = transform(candidate, *args, **kwargs)
    candidate_connectivity = check_connectivity(candidate)
    connectivity = _connectivity_regression(original_connectivity, candidate_connectivity)
    if not connectivity.get("ok", False):
        raise RuntimeError(f"Transformation rejected: connectivity check failed: {connectivity}")
    if verify_equivalence:
        equivalence = check_design_equivalence(original, candidate)
        if not equivalence.get("ok", False) and not _is_solver_inconclusive(equivalence):
            raise RuntimeError(f"Transformation rejected: equivalence check failed: {equivalence}")
    if max_fanout is not None:
        fanout = _fanout_regression(
            check_fanout(original, max_fanout),
            check_fanout(candidate, max_fanout),
            required_bounded_net=fanout_bound_net,
        )
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
    state.last_transform_input = deepcopy(original)
    state.last_transform_result = {
        "transform": getattr(transform, "__name__", str(transform)),
        "result": result,
        "delta": _design_delta(original, candidate),
    }
    return result


def _design_delta(before, after) -> dict[str, Any]:
    before_gate_names = set(before.gates)
    after_gate_names = set(after.gates)
    before_dff_names = set(before.dffs)
    after_dff_names = set(after.dffs)
    before_nets = before.all_nets()
    after_nets = after.all_nets()
    before_report = gate_counts(before)
    after_report = gate_counts(after)
    before_counts = before_report["counts"]
    after_counts = after_report["counts"]
    all_types = sorted(set(before_counts) | set(after_counts))
    type_delta = {
        gate_type: after_counts.get(gate_type, 0) - before_counts.get(gate_type, 0)
        for gate_type in all_types
        if after_counts.get(gate_type, 0) != before_counts.get(gate_type, 0)
    }
    return {
        "before_total_gates": before_report["total"],
        "after_total_gates": after_report["total"],
        "total_gate_delta": after_report["total"] - before_report["total"],
        "type_delta": type_delta,
        "added_gates": sorted(after_gate_names - before_gate_names),
        "removed_gates": sorted(before_gate_names - after_gate_names),
        "added_dffs": sorted(after_dff_names - before_dff_names),
        "removed_dffs": sorted(before_dff_names - after_dff_names),
        "added_nets": sorted(after_nets - before_nets),
        "removed_nets": sorted(before_nets - after_nets),
    }

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


def _fanout_regression(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    required_bounded_net: str | None = None,
) -> dict[str, Any]:
    """Return only fanout-bound violations introduced or worsened by a transform."""
    before_violations = before.get("violations") or {}
    after_violations = after.get("violations") or {}

    new_or_worse = {
        net: count
        for net, count in sorted(after_violations.items())
        if net not in before_violations or count > before_violations[net]
    }
    target_still_violates = (
        {required_bounded_net: after_violations[required_bounded_net]}
        if required_bounded_net is not None and required_bounded_net in after_violations
        else {}
    )

    return {
        "ok": not new_or_worse and not target_still_violates,
        "violations": after_violations,
        "new_or_worse_violations": new_or_worse,
        "target_still_violates": target_still_violates,
    }


def _is_solver_inconclusive(result: dict[str, Any]) -> bool:
    """Return true when equivalence failed only because no scalable solver is available."""
    reason = str(result.get("reason", ""))
    if "z3-solver is not installed" in reason:
        return True

    failures = result.get("failures")
    if not isinstance(failures, dict) or not failures:
        return False
    for failure in failures.values():
        if not isinstance(failure, dict):
            return False
        if "z3-solver is not installed" not in str(failure.get("reason", "")):
            return False
        if failure.get("counterexample") is not None:
            return False
    return True

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


def _format_gate_type_count(result: dict[str, Any]) -> str:
    return f'{result["gate_type"].upper()} gate count: {result["count"]}'


def _format_all_paths(result: dict[str, Any]) -> str:
    lines = [
        f'Combinational paths from "{result["src"]}" to "{result["dst"]}": '
        f'{result["num_paths"]}'
    ]
    if result["truncated"]:
        lines.append(f'Showing first {result["max_paths"]} path(s); enumeration was truncated.')
    for index, path in enumerate(result["paths"], 1):
        lines.append(f'{index}. ' + " -> ".join(path))
    if not result["paths"]:
        lines.append("- none")
    return "\n".join(lines)

def _format_gate_type_connections(result: dict[str, Any]) -> str:
    lines = [
        f'{result["gate_type"].upper()} gate connections: {result["num_gates"]}'
    ]
    if result.get("truncated"):
        lines.append(f'Showing first {result["max_items"]} gate(s); report was truncated.')
    for item in result["gates"]:
        if item["kind"] == "gate":
            inputs = ", ".join(item["inputs"]) or "(none)"
            lines.append(f'- {item["instance"]}: inputs=[{inputs}], output={item["output"]}')
        else:
            pins = ", ".join(f"{pin}={net}" for pin, net in item["pins"].items() if net is not None)
            lines.append(f'- {item["instance"]}: {pins}')
    if not result["gates"]:
        lines.append("- none")
    return "\n".join(lines)


def _format_direct_pi_po_paths(result: dict[str, Any]) -> str:
    lines = [f'Direct PI-to-PO zero-gate paths: {result["num_paths"]}']
    for index, path in enumerate(result["paths"], 1):
        lines.append(f'{index}. ' + " -> ".join(path))
    if not result["paths"]:
        lines.append("- none")
    return "\n".join(lines)


def _format_dffs_by_clock(result: dict[str, Any]) -> str:
    lines = [f'DFFs driven by clock "{result["clock"]}": {result["num_dffs"]}']
    if result.get("truncated"):
        lines.append(f'Showing first {result["max_items"]} DFF(s); report was truncated.')
    for item in result["dffs"]:
        pins = ", ".join(f"{pin}={net}" for pin, net in item["pins"].items() if net is not None)
        lines.append(f'- {item["instance"]}: {pins}')
    if not result["dffs"]:
        lines.append("- none")
    return "\n".join(lines)


def _positive_int_or_default(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default

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


def _format_highest_fanout_primary_input(result: dict[str, Any]) -> str:
    inputs = ", ".join(result["inputs"]) if result["inputs"] else "none"
    return (
        "Primary input(s) with highest fanout: "
        f'{inputs}. Maximum fanout: {result["max_fanout"]}.'
    )


def _format_fanout_cone(result: dict[str, Any]) -> str:
    lines = [
        f'Transitive fanout cone of "{result["source"]}": '
        f'{result["num_gates"]} gate(s), {result["num_nets"]} net(s), '
        f'{result["num_primary_outputs"]} primary output(s), '
        f'{result["num_dff_sinks"]} DFF sink(s).'
    ]
    if result["gates"]:
        lines.append("Reachable gates:")
        lines.extend(f"- {gate}" for gate in result["gates"])
    else:
        lines.append("Reachable gates: none")
    return "\n".join(lines)


def _format_constant_input_gates(result: dict[str, Any]) -> str:
    title_type = result["gate_type"].upper() if result["gate_type"] else "Gate"
    lines = [f"{title_type} gates with constant inputs: {result['num_gates']}"]
    for item in result["gates"]:
        inputs = ", ".join(item["inputs"])
        constants = ", ".join(item["constant_inputs"])
        lines.append(
            f'- {item["name"]} ({item["gate_type"]}), output {item["output"]}, '
            f'inputs [{inputs}], constant input(s): {constants}'
        )
    if not result["gates"]:
        lines.append("- none")
    return "\n".join(lines)


def _format_articulation_points(result: dict[str, Any]) -> str:
    lines = [
        f'Articulation points between "{result["src"]}" and "{result["dst"]}": '
        f'{result["num_points"]}'
    ]
    if result["articulation_points"]:
        lines.extend(f'- {point}' for point in result["articulation_points"])
    else:
        lines.append("- none")
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


def _format_shared_fanin_cone_gates(result: dict[str, Any]) -> str:
    lines = [
        f'Shared fanin cone gates between "{result["target_a"]}" and '
        f'"{result["target_b"]}": {result["num_shared_gates"]}'
    ]
    if result["shared_gates"]:
        lines.extend(f'- {gate}' for gate in result["shared_gates"])
    else:
        lines.append("- none")
    return "\n".join(lines)


def _format_max_depth_to_dff_d(result: dict[str, Any]) -> str:
    if result["dff"] is None:
        return "The maximum logic depth from any primary input to any DFF D-pin is 0. No reachable DFF D-pin was found."
    return (
        "The maximum logic depth from any primary input to any DFF D-pin is "
        f'{result["max_depth"]}.\n'
        f'Example endpoint: DFF {result["dff"]} D-pin {result["d_pin"]}.\n'
        f'Example path: {" -> ".join(result["path"]) if result["path"] else "(none)"}'
    )


def _format_outputs_depth_greater_than(result: dict[str, Any]) -> str:
    lines = [
        f'Primary outputs with logic depth greater than {result["min_depth"]}: '
        f'{result["num_outputs"]}'
    ]
    for item in result["outputs"]:
        lines.append(f'- {item["output"]}: depth {item["depth"]}')
    if not result["outputs"]:
        lines.append("- none")
    return "\n".join(lines)


def _format_last_transform_stats(last_transform: dict[str, Any] | None) -> str:
    if not last_transform:
        return "No transform has been performed yet."
    result = last_transform.get("result", {})
    transform = last_transform.get("transform", "unknown_transform")
    delta = last_transform.get("delta", {})
    if not isinstance(result, dict):
        return f'Last transform "{transform}" completed, but no structured stats are available.'

    lines = [f'Last transform "{transform}" stats:']
    if isinstance(delta, dict) and delta:
        lines.append(
            "- total gate instances: "
            f'{delta.get("before_total_gates", "?")} -> '
            f'{delta.get("after_total_gates", "?")} '
            f'(delta {delta.get("total_gate_delta", "?")})'
        )
        type_delta = delta.get("type_delta") or {}
        if type_delta:
            changes = ", ".join(
                f'{gate_type}: {change:+d}'
                for gate_type, change in sorted(type_delta.items())
            )
            lines.append(f'- gate type delta: {changes}')
        else:
            lines.append("- gate type delta: none")
        lines.append(_format_delta_list("added gates", delta.get("added_gates", [])))
        lines.append(_format_delta_list("removed gates", delta.get("removed_gates", [])))
        lines.append(_format_delta_list("added DFFs", delta.get("added_dffs", [])))
        lines.append(_format_delta_list("removed DFFs", delta.get("removed_dffs", [])))
        lines.append(_format_delta_list("added nets", delta.get("added_nets", [])))
        lines.append(_format_delta_list("removed nets", delta.get("removed_nets", [])))

    if "num_inserted_buffers" in result:
        lines.append(f'- transform-reported inserted BUF gates: {result["num_inserted_buffers"]}')
    if "num_removed_gates" in result:
        lines.append(f'- transform-reported removed gates: {result["num_removed_gates"]}')
    if "removed_gate_count" in result:
        lines.append(f'- transform-reported removed gates: {result["removed_gate_count"]}')
    if "num_changed" in result:
        lines.append(f'- transform-reported changed items: {result["num_changed"]}')
    if "num_merged" in result:
        lines.append(f'- transform-reported merged gates: {result["num_merged"]}')
    if "num_changed_nets" in result:
        lines.append(f'- transform-reported changed nets: {result["num_changed_nets"]}')
    if "changed" in result:
        lines.append(_format_change_sample(result["changed"], label="Sample transform changes"))
    return "\n".join(lines)


def _format_delta_list(label: str, items: Any, limit: int = 8) -> str:
    if not isinstance(items, list) or not items:
        return f'- {label}: none'
    sample = items[:limit]
    suffix = f' (+{len(items) - limit} more)' if len(items) > limit else ""
    return f'- {label}: {sample}{suffix}'

def _format_change_sample(
    changes: list[Any],
    *,
    label: str = "Sample changes",
    limit: int = 5,
) -> str:
    if not changes:
        return f"{label}: none."
    sample = changes[:limit]
    remaining = len(changes) - len(sample)
    suffix = f" (+{remaining} more)" if remaining > 0 else ""
    return f"{label}: {sample}{suffix}."


def _format_register_paths(result: dict[str, Any]) -> str:
    lines = [f'Register path report: {result["num_dffs"]} DFF(s).']
    lines.append(f'Clock domains: {result["clock_domains"]}')
    lines.append(f'DFF-to-DFF paths: {len(result["dff_to_dff"])}')
    for item in result["dff_to_dff"]:
        lines.append(f'- {item["src_dff"]} -> {item["dst_dff"]}')
    lines.append(f'PI-to-DFF paths: {len(result["pi_to_dff"])}')
    for item in result["pi_to_dff"]:
        lines.append(f'- {item["src_input"]} -> {item["dst_dff"]}')
    lines.append(f'DFF-to-primary-output paths: {len(result["dff_to_primary_output"])}')
    for item in result["dff_to_primary_output"]:
        lines.append(f'- {item["src_dff"]} -> {item["dst_output"]}')
    return "\n".join(lines)

def _format_equivalence_result(expr: str, target: str, result: dict[str, Any]) -> str:
    constant = _constant_expr_label(expr)
    if constant is not None:
        if result.get("ok"):
            return (
                f'Yes. "{target}" is always {constant} regardless of all inputs. '
                f'Checked with {result.get("engine")} engine.'
            )
        lines = [
            f'No. "{target}" is not always {constant}.',
            _format_counterexample(result),
        ]
        if result.get("reason"):
            lines.append(f'Reason: {result["reason"]}')
        return "\n".join(line for line in lines if line)

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
    if _is_solver_inconclusive(result):
        return (
            "Equivalence to original loaded netlist is inconclusive because "
            "z3-solver is not installed and brute-force fallback is too small "
            "for this design."
        )
    lines = [
        "Not equivalent to original loaded netlist.",
        _format_counterexample(result),
    ]
    if result.get("reason"):
        lines.append(f'Reason: {result["reason"]}')
    return "\n".join(line for line in lines if line)


def _format_last_transform_equivalence_result(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return (
            "Equivalent to the pre-transformation netlist. "
            f'Checked with {result.get("engine")} engine over combinational boundaries.'
        )
    if _is_solver_inconclusive(result):
        return (
            "Equivalence to the pre-transformation netlist is inconclusive because "
            "z3-solver is not installed and brute-force fallback is too small "
            "for this design."
        )
    lines = [
        "Not equivalent to the pre-transformation netlist.",
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


def _constant_expr_label(expr: str) -> str | None:
    normalized = expr.strip()
    if normalized in {"0", "1'b0"}:
        return "0"
    if normalized in {"1", "1'b1"}:
        return "1"
    return None


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
