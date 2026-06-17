from __future__ import annotations

import re
from typing import Any


SUPPORTED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_path",
    "all_paths_pass_through",
    "report_all_paths",
    "all_paths",
    "report_direct_pi_po_paths",
    "check_cut_signal",
    "max_depth",
    "report_max_logic_depth",
    "report_cone_depth",
    "logic_cone",
    "report_gate_counts",
    "report_gate_type_count",
    "report_primary_inputs",
    "report_primary_outputs",
    "report_fanout",
    "report_highest_fanout_primary_input",
    "report_gate_connections",
    "report_gate_type_connections",
    "report_gates_by_type",
    "report_gate_type_count_in_cone",
    "report_deepest_output_cone",
    "report_largest_fanin_cone_output",
    "report_outputs_by_cone_size",
    "report_fanout_cone",
    "report_constant_input_gates",
    "report_io_counts",
    "gate_on_max_depth_path",
    "report_articulation_points",
    "report_shared_fanin_cone_gates",
    "derive_boolean_equation",
    "find_nand_equivalent_pair",
    "report_max_depth_to_dff_d",
    "report_max_register_to_register_depth",
    "report_outputs_depth_greater_than",
    "report_register_paths",
    "report_dff_input_logic_structures",
    "report_last_transform_stats",
    "find_gates",
    "same_clock_domain",
    "report_dffs_by_clock",
    "replace_buffers_with_and",
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
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalent_to_original",
    "check_equivalent_to_last_transform_input",
    "check_equivalence",
    "check_property",
    "check_signal_symmetry",
}

_SIGNAL_RE = r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?"


def _depth_optimization_args(max_depth: int | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "cost_function": "max_logic_depth",
        "objective": "minimize",
        "cost_scope": "whole_design",
    }
    if max_depth is not None:
        args["max_depth"] = max_depth
    return args


def plan_request(user_request: str, state) -> dict[str, Any]:
    """
    Convert one natural-language request into a Tool API plan.

    Args:
        user_request:
            One command read from stdin, for example
            "Find all the buffers which name include '_gc__'".
        state:
            Runtime state passed by main.py. The Day1/Day2 rule planner does not
            need it yet, but the argument is kept for future context-aware rules.

    Returns:
        A dict that matches docs/tool_spec.md, such as
        {"op": "read_design", "args": {"path": "..."}}, or an unsupported plan
        when no deterministic rule can map the request.

    Routing order:
        1. testcase initialization
        2. Verilog read/write
        3. netlist transformations
        4. analysis queries
        5. verification checks
    """
    del state  # Reserved for future context-aware planning.

    text = user_request.strip()
    low = text.lower()

    if not text:
        return _unsupported("Empty request.")

    testcase_plan = _plan_begin_testcase(text, low)
    if testcase_plan:
        return testcase_plan

    io_plan = _plan_design_io(text, low)
    if io_plan:
        return io_plan

    transform_plan = _plan_transform(text, low)
    if transform_plan:
        return transform_plan

    analysis_plan = _plan_analysis(text, low)
    if analysis_plan:
        return analysis_plan

    verify_plan = _plan_verification(text, low)
    if verify_plan:
        return verify_plan

    return _unsupported("Could not map request to a supported operation in the Day1 planner.")


def _plan_begin_testcase(text: str, low: str) -> dict[str, Any] | None:
    """
    Route requests that start a new testcase.

    Args:
        text:
            Original request text. Case and punctuation are preserved so the
            testcase name can be extracted exactly as written.
        low:
            Lowercase request text for keyword matching, such as "beginning",
            "new", "start", and "testcase".

    Returns:
        A begin_testcase plan if the request starts a testcase. Otherwise None,
        which lets plan_request try the next route.
    """
    if "testcase" not in low and "test case" not in low:
        return None
    if not any(word in low for word in ("begin", "beginning", "new", "start", "initialize", "initialise")):
        return None

    case_name = _extract_case_name(text) or "unknown_case"
    return {"op": "begin_testcase", "args": {"case_name": case_name}}


def _plan_design_io(text: str, low: str) -> dict[str, Any] | None:
    """
    Route Verilog file input/output requests.

    Args:
        text:
            Original request text. The path extractor uses this to preserve file
            paths such as 'tests/design/netlist/test8.v'.
        low:
            Lowercase request text for intent keywords. Read intents include
            load/read/parse/open/import. Write intents include
            write/output/save/dump/export.

    Returns:
        read_design or write_design when a .v path and matching intent are
        found. Returns an unsupported plan if a .v path is mentioned but cannot
        be extracted. Returns None when this is not a file IO request.
    """
    if ".v" not in low:
        return None

    path = _extract_quoted_path(text) or _extract_directory_file_path(text) or _extract_verilog_path(text)
    if path is None:
        return _unsupported("A Verilog file path was mentioned but no .v path could be extracted.")

    if any(word in low for word in ("write", "output", "save", "dump", "export")):
        return {"op": "write_design", "args": {"path": path}}

    if any(word in low for word in ("load", "read", "parse", "open", "import")):
        return {"op": "read_design", "args": {"path": path}}

    return None


def _plan_transform(text: str, low: str) -> dict[str, Any] | None:
    """
    Route netlist transformation requests.

    Args:
        text:
            Original request text. This is used to extract explicit instance
            names and the extra input signal, such as _gc_ctrl.
        low:
            Lowercase request text. The supported Day2 transform rule is
            replace + buffer(s) + AND.

    Returns:
        A replace_buffers_with_and plan. The args contain extra_input plus
        either explicit targets or targets_from="found_buffers". Returns None
        when no supported transformation rule matches.
    """
    reconnect = _extract_reconnect_request(text, low)
    if reconnect:
        gate, pin, new_net = reconnect
        return {"op": "reconnect_gate_input", "args": {"gate": gate, "pin": pin, "new_net": new_net}}

    rename_gate_pair = _extract_rename_gate_pair(text, low)
    if rename_gate_pair:
        old_name, new_name = rename_gate_pair
        return {"op": "rename_gate", "args": {"old_name": old_name, "new_name": new_name}}

    rename_pair = _extract_rename_net_pair(text, low)
    if rename_pair:
        old_net, new_net = rename_pair
        return {"op": "rename_net", "args": {"old_net": old_net, "new_net": new_net}}

    if (
        "constant propagation" in low
        or "propagate constants" in low
        or "propagate constant" in low
        or ("simplify" in low and "constant" in low)
        or "tied constant" in low
        or "tied constants" in low
    ):
        return {"op": "constant_propagation", "args": {}}

    if (
        ("replace" in low or "simplify" in low)
        and "nand" in low
        and ("constant 1" in low or "constant-1" in low or "1'b1" in low)
        and ("inverter" in low or "inverters" in low or "not" in low)
    ):
        return {"op": "replace_nand_const1_with_not", "args": {}}

    if (
        ("replace" in low or "rewrite" in low or "remap" in low or "convert" in low)
        and "xnor" in low
        and ("nor-only" in low or "nor only" in low or ("nor" in low and "only" in low))
    ):
        return {"op": "replace_xnor_with_nor", "args": {}}

    if (
        ("reconstruct" in low or "rewrite" in low or "remap" in low or "convert" in low)
        and ("entire netlist" in low or "full netlist" in low or "design" in low or "netlist" in low)
        and _mentions_and_not_gate_set(low)
        and "not" in low
        and ("only" in low or "using" in low)
    ):
        return {"op": "replace_with_and_not", "args": {}}

    if "replace" in low and ("buffer" in low or "buffers" in low) and _mentions_gate_type(low, "and"):
        extra_input = _extract_extra_input(text) or "_gc_ctrl"
        targets = _extract_instance_list(text)

        args: dict[str, Any] = {"extra_input": extra_input}
        if targets and "found" not in low:
            args["targets"] = targets
        else:
            args["targets_from"] = "found_buffers"

        return {"op": "replace_buffers_with_and", "args": args}

    if (
        any(word in low for word in ("remove", "eliminate", "delete", "sweep", "prune"))
        and (
            "dangling" in low
            or "unused" in low
            or "floating" in low
            or "do not affect" in low
            or "does not affect" in low
            or "do not contribute" in low
            or "does not contribute" in low
            or "not contribute" in low
            or "not connected to any primary output" in low
        )
    ):
        return {"op": "remove_dangling", "args": {}}

    if (
        ("back-to-back" in low or "back to back" in low)
        and ("inverter" in low or "inverters" in low or "not" in low)
        and ("collapse" in low or "remove" in low or "wire" in low)
    ):
        return {"op": "collapse_back_to_back_inverters", "args": {}}

    if (
        ("replace" in low or "collapse" in low or "merge" in low)
        and ("inverter" in low or "inverters" in low or "inv" in low)
        and ("buffer" in low or "buffers" in low or "buf" in low)
    ):
        return {"op": "replace_inv_buf_with_inv", "args": {}}

    if (
        ("restructure" in low or "rewrite" in low or "convert" in low)
        and "cone" in low
        and "nand" in low
        and "not" in low
    ):
        target = _extract_after_keyword(text, "output") or _extract_after_keyword(text, "cone of")
        target = target or _extract_after_keyword(text, "of") or _extract_after_keyword(text, "target")
        if target:
            return {"op": "replace_or_with_nand_not", "args": {"cone_target": target}}

    if (
        "replace" in low
        and _mentions_gate_type(low, "or")
        and "nand" in low
        and ("not" in low or "inverter" in low or "inverters" in low)
    ):
        target = _extract_after_keyword(text, "cone of") or _extract_after_keyword(text, "of")
        target = target or _extract_after_keyword(text, "for") or _extract_after_keyword(text, "target")
        if target:
            return {"op": "replace_or_with_nand_not", "args": {"cone_target": target}}

    if (
        any(word in low for word in ("insert", "add", "build", "perform", "optimize"))
        and ("buffer" in low or "buffers" in low or "fanout optimization" in low)
        and ("fanout" in low or "fan-out" in low or "loads" in low or "drives more than" in low)
    ):
        max_fanout = _extract_limit_int(text)
        if (
            max_fanout is not None
            and (
                "all high-fanout" in low
                or "all high fanout" in low
                or "all nets" in low
                or "every net" in low
                or "entire design" in low
                or "across the design" in low
                or "across the netlist" in low
                or "wherever" in low
                or "wherever needed" in low
                or "no gate drives more than" in low
                or "fanout optimization" in low
            )
        ):
            return {"op": "insert_buffers_for_all_high_fanout", "args": {"max_fanout": max_fanout}}

        net = _extract_after_keyword(text, "net") or _extract_after_keyword(text, "signal")
        net = net or _extract_after_keyword(text, "on") or _extract_after_keyword(text, "for")
        if net and max_fanout is not None:
            return {"op": "insert_buffers_for_fanout", "args": {"net": net, "max_fanout": max_fanout}}

    if "balance" in low and "depth" in low and ("buffer" in low or "buffers" in low):
        src = _extract_after_keyword(text, "source") or _extract_after_keyword(text, "from")
        dsts = _extract_destination_list(text)
        if src and dsts:
            return {
                "op": "balance_depth_with_buffers",
                "args": {"src": src, "dsts": dsts, "minimize_buffers": True},
            }

    if (
        ("for each output" in low or "each output" in low)
        and "optimize" in low
        and "depth" in low
    ):
        args = _depth_optimization_args(_extract_limit_int(text))
        return {"op": "optimize_design_depth", "args": args}

    if "optimize" in low and ("logic cone" in low or "cone" in low):
        target = _extract_after_keyword(text, "cone of") or _extract_after_keyword(text, "of")
        target = target or _extract_after_keyword(text, "target") or _extract_after_keyword(text, "for")
        if target:
            args: dict[str, Any] = {
                "target": target,
                "minimize_gate_count": "gate count" in low or "minimize" in low or "reduce" in low,
            }
            if "depth" in low:
                max_allowed_depth = _extract_limit_int(text)
                if max_allowed_depth is not None:
                    args["max_depth"] = max_allowed_depth
            return {"op": "optimize_cone", "args": args}

    if (
        ("restructure" in low or "optimize" in low)
        and ("target depth" in low or "levels deep" in low or "at most" in low)
        and re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?\b", text)
    ):
        target = _extract_after_keyword(text, "restructure") or _extract_after_keyword(text, "optimize")
        target = target or _extract_after_keyword(text, "output") or _extract_after_keyword(text, "signal")
        if target and target.lower() not in {"logic", "the", "design", "its"}:
            args: dict[str, Any] = {"target": target, "minimize_gate_count": True}
            max_allowed_depth = _extract_limit_int(text)
            if max_allowed_depth is not None:
                args["max_depth"] = max_allowed_depth
            return {"op": "optimize_cone", "args": args}

    if "optimize" in low and re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?\b", text):
        target = _extract_after_keyword(text, "optimize")
        if target and target.lower() not in {"logic", "the", "design", "its"}:
            args: dict[str, Any] = {"target": target, "minimize_gate_count": True}
            max_allowed_depth = _extract_limit_int(text)
            if max_allowed_depth is not None:
                args["max_depth"] = max_allowed_depth
            return {"op": "optimize_cone", "args": args}

    if (
        any(word in low for word in ("insert", "add"))
        and ("buf" in low or "buffer" in low)
        and ("dedicated buffer" in low or "each load" in low)
    ):
        net = _extract_after_keyword(text, "signal") or _extract_after_keyword(text, "net")
        net = net or _extract_after_keyword(text, "on")
        if net:
            return {"op": "insert_dedicated_buffers_for_each_load", "args": {"net": net}}

    if (
        ("optimize" in low or "optimization" in low)
        and ("depth" in low or "critical path" in low or "maximum path" in low)
        and (
            "design" in low
            or "logic" in low
            or "combinational logic" in low
            or "perform depth" in low
            or "critical path" in low
            or "maximum path" in low
        )
    ):
        args = _depth_optimization_args(_extract_limit_int(text))
        return {"op": "optimize_design_depth", "args": args}

    if (
        ("reduce" in low or "minimize" in low or "shorten" in low)
        and ("critical path" in low or "path depth" in low or "maximum path depth" in low)
        and ("restructuring" in low or "logic" in low or "depth" in low)
    ):
        args = _depth_optimization_args(_extract_limit_int(text))
        return {"op": "optimize_design_depth", "args": args}

    if (
        ("replace" in low or "rewrite" in low or "remap" in low or "convert" in low)
        and "xor" in low
        and (
            "nand-only" in low
            or "nand only" in low
            or "4-nand" in low
            or "four-nand" in low
            or "nand circuit" in low
            or ("nand" in low and "only" in low)
        )
    ):
        return {"op": "replace_xor_with_nand", "args": {}}

    if (
        ("replace" in low or "rewrite" in low or "remap" in low)
        and ("xnor" in low or "nor" in low)
        and ("basic" in low or "xor" in low or "or" in low or "not" in low or "gates" in low)
    ):
        return {"op": "replace_xnor_nor_with_basic_gates", "args": {}}

    if (
        "nand-only" in low
        or "nand only" in low
        or "map to nand" in low
        or "remap to nand" in low
        or (("replace" in low or "rewrite" in low or "remap" in low) and "and" in low and "not" in low and "nand" in low)
    ):
        return {"op": "replace_and_not_with_nand", "args": {}}

    if (
        ("merge" in low or "remove" in low)
        and ("equivalent" in low or "duplicate" in low or "identical" in low or "redundant" in low)
        and ("gate" in low or "gates" in low)
    ):
        return {"op": "merge_equivalent_gates", "args": {}}

    return None


def _plan_analysis(text: str, low: str) -> dict[str, Any] | None:
    """
    Route analysis and query requests.

    Args:
        text:
            Original request text. This preserves signal names, gate patterns,
            quoted strings, and endpoint names such as in0/out3.
        low:
            Lowercase request text for matching analysis phrases like "find",
            "logic cone", "max depth", and "path".

    Returns:
        One of the supported analysis plans: find_gates, logic_cone, max_depth,
        or find_path. Returns None when this is not an analysis request.
    """
    if "paths of length 0" in low or "direct wire connection" in low:
        return {"op": "report_direct_pi_po_paths", "args": {}}

    if ("successor" in low or "successors" in low or "driven by" in low) and "gate" in low:
        gate = _extract_after_keyword(text, "gate") or _extract_after_keyword(text, "by")
        if gate:
            return {"op": "report_fanout", "args": {"net": gate}}

    if "cut" in low and ("primary input" in low or "primary output" in low):
        signal = _extract_after_keyword(text, "wire") or _extract_after_keyword(text, "signal")
        signal = signal or _extract_after_keyword(text, "net")
        if signal:
            return {"op": "check_cut_signal", "args": {"signal": signal}}

    if "symmetric" in low and "respect to" in low:
        symmetry = _extract_symmetry_request(text)
        if symmetry:
            target, input_a, input_b = symmetry
            return {
                "op": "check_signal_symmetry",
                "args": {"target": target, "input_a": input_a, "input_b": input_b},
            }

    if (
        ("primary inputs" in low or "primary input" in low)
        and ("bit width" in low or "bit widths" in low or "width" in low or "widths" in low)
        and ("list" in low or "report" in low)
    ):
        return {"op": "report_primary_inputs", "args": {}}

    if (
        ("primary outputs" in low or "primary output" in low)
        and ("bit width" in low or "bit widths" in low or "width" in low or "widths" in low)
        and ("list" in low or "report" in low)
    ):
        return {"op": "report_primary_outputs", "args": {}}

    if (
        ("d input logic" in low or "d-input logic" in low or "d pin logic" in low or "d-pin logic" in low)
        and ("flip-flop" in low or "flip flops" in low or "flip-flops" in low or "dff" in low or "dffs" in low)
    ):
        return {"op": "report_dff_input_logic_structures", "args": {}}

    if (
        ("enable" in low or "hold" in low)
        and ("flip-flop" in low or "flip flops" in low or "flip-flops" in low or "dff" in low or "dffs" in low)
    ):
        return {"op": "report_dff_input_logic_structures", "args": {}}

    if (
        ("flip-flop" in low or "flip flops" in low or "flip-flops" in low or "dff" in low or "dffs" in low)
        and ("driven by clock" in low or "clock" in low)
    ):
        clock = _extract_after_keyword(text, "clock") or _extract_after_keyword(text, "by")
        if clock:
            return {"op": "report_dffs_by_clock", "args": {"clock": clock}}

    if "deepest" in low and ("output bit" in low or "primary output" in low or "fanin logic cone" in low):
        return {"op": "report_deepest_output_cone", "args": {}}

    if (
        ("largest" in low or "biggest" in low)
        and ("fanin cone" in low or "fan-in cone" in low or "logic cone" in low)
        and ("output" in low or "primary output" in low)
    ):
        return {"op": "report_largest_fanin_cone_output", "args": {}}

    if (
        ("register-to-register" in low or "register to register" in low)
        and "depth" in low
    ):
        return {"op": "report_max_register_to_register_depth", "args": {}}

    if (
        (
            "maximum combinational logic depth" in low
            or "maximum logic depth in the design" in low
            or "maximum combinational depth" in low
        )
        and "dff d" not in low
        and "d-pin" not in low
        and "d pin" not in low
    ):
        return {"op": "report_max_logic_depth", "args": {}}

    if (
        (
            "constant input" in low
            or "constant inputs" in low
            or "constant 0 input" in low
            or "constant-0 input" in low
            or "constant 1 input" in low
            or "constant-1 input" in low
        )
        and ("report" in low or "list" in low or "find" in low)
        and ("gate" in low or "gates" in low or "nand" in low)
    ):
        gate_type = "nand" if "nand" in low else _extract_gate_type(low)
        return {"op": "report_constant_input_gates", "args": {"gate_type": gate_type}}

    if (
        ("added" in low or "eliminated" in low or "removed" in low)
        and ("how many" in low or "count" in low or "number" in low)
        and ("gate" in low or "gates" in low or "buf" in low or "nand" in low or "nor" in low)
    ):
        return {"op": "report_last_transform_stats", "args": {}}

    if (
        ("gate" in low or "gates" in low)
        and (
            "broken down" in low
            or "by type" in low
            or "all the gates" in low
            or "total count" in low
            or "total number of gates" in low
        )
    ):
        return {"op": "report_gate_counts", "args": {}}

    if (
        ("how many" in low or "count" in low or "number" in low)
        and "cone" in low
        and ("gate" in low or "gates" in low)
    ):
        gate_type = _extract_gate_type(low)
        target = _extract_after_keyword(text, "output") or _extract_after_keyword(text, "cone of")
        target = target or _extract_after_keyword(text, "of")
        if gate_type and target:
            return {
                "op": "report_gate_type_count_in_cone",
                "args": {"target": target, "gate_type": gate_type},
            }

    if "nand" in low and "pair" in low and "equivalent" in low:
        target = _extract_after_keyword(text, "equivalent to") or _extract_after_keyword(text, "to")
        target = target or _extract_after_keyword(text, "output") or _extract_after_keyword(text, "signal")
        if target:
            return {"op": "find_nand_equivalent_pair", "args": {"target": target}}

    if ("list" in low or "report" in low or "find" in low) and "gates" in low and ("input" in low or "output" in low or "signals" in low):
        gate_type = _extract_gate_type(low)
        if gate_type is not None:
            return {"op": "report_gate_type_connections", "args": {"gate_type": gate_type}}

    if (
        ("list" in low or "report" in low)
        and "gates" in low
        and "shared" not in low
        and "fanin" not in low
        and "count" not in low
        and "how many" not in low
        and "number" not in low
    ):
        gate_type = _extract_gate_type(low)
        if gate_type and "constant" not in low and "connected" not in low:
            return {"op": "report_gates_by_type", "args": {"gate_type": gate_type}}
    if "shared" in low and "fanin" in low and ("cone" in low or "cones" in low):
        targets = _extract_targets_after_between_or_of(text)
        if targets:
            target_a, target_b = targets
            return {
                "op": "report_shared_fanin_cone_gates",
                "args": {"target_a": target_a, "target_b": target_b},
            }

    if (
        ("derive" in low and "boolean equation" in low)
        or "logic expression" in low
        or "boolean function" in low
        or ("function" in low and "compute" in low)
    ):
        target = _extract_after_keyword(text, "output") or _extract_after_keyword(text, "signal")
        target = target or _extract_after_keyword(text, "for")
        if target:
            return {"op": "derive_boolean_equation", "args": {"target": target}}

    if "dff" in low and ("d-pin" in low or "d pin" in low) and "depth" in low:
        return {"op": "report_max_depth_to_dff_d", "args": {}}

    if "outputs" in low and "depth" in low and any(
        phrase in low for phrase in ("greater than", "more than", "larger than", "over")
    ):
        min_depth = _extract_limit_int(text)
        if min_depth is not None:
            return {"op": "report_outputs_depth_greater_than", "args": {"min_depth": min_depth}}

    if "buf" in low and "added" in low and ("how many" in low or "count" in low or "number" in low):
        return {"op": "report_last_transform_stats", "args": {}}

    if "always 0" in low or "always zero" in low:
        target = _extract_after_keyword(text, "output") or _extract_after_keyword(text, "signal")
        if target:
            return {"op": "check_equivalence", "args": {"expr": "0", "target": target}}

    if (
        ("count" in low or "number" in low or "how many" in low)
        and ("gate" in low or "gates" in low or "cell" in low or "cells" in low or "register" in low or "dff" in low)
    ):
        gate_type = _extract_gate_type_before_count(low) or _extract_gate_type(low)
        if gate_type is not None:
            return {"op": "report_gate_type_count", "args": {"gate_type": gate_type}}
        return {"op": "report_gate_counts", "args": {}}

    if (
        ("number" in low or "count" in low or "how many" in low)
        and ("primary inputs" in low or "primary input" in low)
        and ("outputs" in low or "primary outputs" in low or "primary output" in low)
    ):
        return {"op": "report_io_counts", "args": {}}

    if "maximum-depth path" in low or "maximum depth path" in low or "max-depth path" in low:
        gate = _extract_connection_instance(text) or _extract_after_keyword(text, "gate")
        if gate:
            return {"op": "gate_on_max_depth_path", "args": {"gate": gate}}

    if (
        ("register-to-register" in low or "register to register" in low)
        and ("path" in low or "paths" in low)
    ):
        return {"op": "report_register_paths", "args": {}}

    if "primary input" in low and "highest fanout" in low:
        return {"op": "report_highest_fanout_primary_input", "args": {}}

    if "articulation" in low and ("between" in low or "from" in low):
        targets = _extract_targets_after_between_or_of(text)
        if targets:
            src, dst = targets
            return {"op": "report_articulation_points", "args": {"src": src, "dst": dst}}

    if "transitive fanout" in low or "reachable from" in low or "fanout cone" in low:
        source = _extract_after_keyword(text, "fanout of") or _extract_after_keyword(text, "from")
        source = source or _extract_after_keyword(text, "input") or _extract_after_keyword(text, "source")
        if source:
            return {"op": "report_fanout_cone", "args": {"source": source}}

    if "fanout" in low or "fan-out" in low or "driven by" in low or "loads of" in low:
        net = _extract_after_keyword(text, "fanout of") or _extract_after_keyword(text, "fan-out of")
        net = net or _extract_after_keyword(text, "loads of")
        net = net or _extract_after_keyword(text, "net") or _extract_after_keyword(text, "signal")
        net = net or _extract_after_keyword(text, "driven by")
        if net:
            return {"op": "report_fanout", "args": {"net": net}}

    if ("connected to" in low or "connect to" in low) and ("output of" in low or "signal" in low):
        net = _extract_after_keyword(text, "output of") or _extract_after_keyword(text, "signal")
        if net:
            return {"op": "report_fanout", "args": {"net": net}}

    if (
        ("connection" in low or "connections" in low or "pin" in low or "pins" in low)
        and ("gate" in low or "instance" in low or "dff" in low)
    ):
        gate = _extract_connection_instance(text) or _extract_after_keyword(text, "instance")
        gate = gate or _extract_after_keyword(text, "dff") or _extract_after_keyword(text, "of")
        if gate:
            return {"op": "report_gate_connections", "args": {"gate": gate}}

    if ("buffer" in low or "buffers" in low or "gate" in low or "gates" in low) and "find" in low:
        gate_type = _extract_gate_type(low)
        name_contains = _extract_name_pattern(text)
        plan: dict[str, Any] = {
            "op": "find_gates",
            "args": {"gate_type": gate_type, "name_contains": name_contains},
        }
        if gate_type == "buf" or "buffer" in low or "buffers" in low:
            plan["save_as"] = "found_buffers"
        return plan

    if (
        ("primary output" in low or "primary outputs" in low or "outputs" in low)
        and ("logic cone" in low or "fanin cone" in low or "fan-in cone" in low)
        and any(word in low for word in ("more than", "greater than", "over", "larger than", "contains"))
    ):
        min_gates = _extract_limit_int(text)
        if min_gates is not None:
            return {"op": "report_outputs_by_cone_size", "args": {"min_gates": min_gates}}

    if "clock domain" in low:
        dff_pair = _extract_dff_pair(text)
        if dff_pair:
            dff_a, dff_b = dff_pair
            return {"op": "same_clock_domain", "args": {"dff_a": dff_a, "dff_b": dff_b}}

    if "depth" in low and ("logic cone" in low or "fanin cone" in low or "fan-in cone" in low or "cone of" in low):
        target = _extract_after_keyword(text, "cone of") or _extract_after_keyword(text, "of")
        target = target or _extract_after_keyword(text, "target") or _extract_after_keyword(text, "output")
        if target:
            return {"op": "report_cone_depth", "args": {"target": target}}

    if "logic cone" in low or "fanin cone" in low or "fan-in cone" in low:
        target = _extract_after_keyword(text, "of") or _extract_after_keyword(text, "for")
        target = target or _extract_after_keyword(text, "target")
        if target:
            return {"op": "logic_cone", "args": {"target": target}}

    if "every" in low and "path" in low and "from" in low and "to" in low and "through" in low:
        endpoints = _extract_src_dst(text)
        node = _extract_after_keyword(text, "through")
        if endpoints and node:
            src, dst = endpoints
            return {"op": "all_paths_pass_through", "args": {"src": src, "dst": dst, "node": node}}

    if (
        (
            "all paths" in low
            or "every path" in low
            or "list paths" in low
            or "enumerate paths" in low
            or "enumeration of paths" in low
        )
        and (("from" in low and "to" in low) or "between" in low)
    ):
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            limit = _extract_path_limit_int(text)
            args: dict[str, Any] = {"src": src, "dst": dst}
            if limit is not None:
                args["max_paths"] = limit
            return {"op": "report_all_paths", "args": args}

    if (
        "maximum logic depth" in low
        or "max logic depth" in low
        or "max depth" in low
        or "critical path depth" in low
        or "longest combinational path depth" in low
    ):
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            return {"op": "max_depth", "args": {"src": src, "dst": dst}}

    if "path" in low and "from" in low and "to" in low:
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            avoid = _extract_avoid_list(text)
            args: dict[str, Any] = {"src": src, "dst": dst}
            if avoid:
                args["avoid"] = avoid
            return {"op": "find_path", "args": args}

    if "path" in low and ("originating" in low or "terminating" in low or "connecting" in low):
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            if "every" in low or "all" in low or "list" in low:
                return {"op": "all_paths", "args": {"src": src, "dst": dst}}
            args = {"src": src, "dst": dst}
            avoid = _extract_avoid_list(text)
            if avoid:
                args["avoid"] = avoid
            return {"op": "find_path", "args": args}

    if "depend" in low and "input" in low and "output" in low:
        src = _extract_after_keyword(text, "input")
        dst = _extract_after_keyword(text, "output")
        if src and dst:
            return {"op": "find_path", "args": {"src": src, "dst": dst}}

    return None


def _plan_verification(text: str, low: str) -> dict[str, Any] | None:
    """
    Route verification and checking requests.

    Args:
        text:
            Original request text. This is used to extract numeric limits and
            endpoint signals, for example "from in0 to out3 <= 5".
        low:
            Lowercase request text. This route only handles requests containing
            "check" or "verify".

    Returns:
        check_connectivity, check_fanout, or check_depth. Returns an unsupported
        plan when fanout is requested without a numeric limit. Returns None when
        this is not a verification request.
    """
    if (
        "check" not in low
        and "verify" not in low
        and "prove" not in low
        and "confirm" not in low
        and "equivalent" not in low
        and "equivalence" not in low
        and "identical logic" not in low
        and "same logic" not in low
        and "floating" not in low
        and "unconnected" not in low
    ):
        return None

    if "connectivity" in low or "connection" in low or "floating" in low or "driver" in low:
        return {"op": "check_connectivity", "args": {}}

    if (
        ("equivalent" in low or "equivalence" in low)
        and ("original" in low or "loaded netlist" in low or "loaded design" in low or "last loaded" in low)
    ):
        return {"op": "check_equivalent_to_original", "args": {}}

    if (
        ("equivalent" in low or "equivalence" in low)
        and ("pre-transformation" in low or "pre transformation" in low or "previous transform" in low)
    ):
        return {"op": "check_equivalent_to_last_transform_input", "args": {}}

    if "equivalent" in low or "equivalence" in low:
        signal_pair = _extract_signal_equivalence_pair(text)
        if signal_pair:
            expr, target = signal_pair
            return {"op": "check_equivalence", "args": {"expr": expr, "target": target}}
        equivalence = _extract_equivalence(text)
        if equivalence:
            expr, target = equivalence
            return {"op": "check_equivalence", "args": {"expr": expr, "target": target}}

    if "identical logic" in low or "same logic" in low or "same logical" in low:
        signal_pair = _extract_signal_pair(text)
        if signal_pair:
            expr, target = signal_pair
            return {"op": "check_equivalence", "args": {"expr": expr, "target": target}}

    if "property" in low or "asserted only when" in low or "only when" in low:
        prop = _extract_property(text)
        if prop:
            target, property_text = prop
            return {"op": "check_property", "args": {"target": target, "property": property_text}}

    if "fanout" in low or "fan-out" in low:
        max_fanout = _extract_limit_int(text)
        if max_fanout is None:
            return _unsupported("Fanout check needs a numeric max_fanout bound.")
        return {"op": "check_fanout", "args": {"max_fanout": max_fanout}}

    if "depth" in low:
        endpoints = _extract_src_dst(text)
        max_depth = _extract_limit_int(text)
        if endpoints and max_depth is not None:
            src, dst = endpoints
            return {
                "op": "check_depth",
                "args": {"src": src, "dst": dst, "max_depth": max_depth},
            }

    return None


def _unsupported(reason: str) -> dict[str, Any]:
    return {"op": "unsupported", "args": {"reason": reason}}


def _extract_case_name(text: str) -> str | None:
    patterns = [
        r"case\s+name\s+is\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"test\s*case\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"testcase\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_quoted_path(text: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+\.v)['\"]", text, flags=re.I)
    return match.group(1) if match else None


def _extract_verilog_path(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9_./\\:\-]+\.v)", text, flags=re.I)
    return match.group(1) if match else None


def _extract_directory_file_path(text: str) -> str | None:
    file_match = re.search(r"\bfile\s+['\"]?([A-Za-z0-9_.\-]+\.v)['\"]?", text, flags=re.I)
    dir_match = re.search(
        r"\bdirectory\s+['\"]?([A-Za-z0-9_./\\:\- ]+?)['\"]?(?:[.?!]|$)",
        text,
        flags=re.I,
    )
    if not file_match or not dir_match:
        return None

    directory = dir_match.group(1).strip().rstrip("/\\")
    filename = file_match.group(1)
    separator = "\\" if "\\" in directory else "/"
    return f"{directory}{separator}{filename}"


def _extract_name_pattern(text: str) -> str | None:
    quoted = _extract_quoted_text(text)
    if quoted:
        return quoted

    patterns = [
        r"(?:include|includes|including|contains?|like|named?)\s+([A-Za-z0-9_$*?_\-]+)",
        r"name\s+.*?\b([A-Za-z0-9_$*?_\-]*__[A-Za-z0-9_$*?_\-]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).replace("*", "")

    if "_gc__" in text:
        return "_gc__"
    return None


def _extract_quoted_text(text: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def _extract_gate_type(low: str) -> str | None:
    aliases = [
        ("xnor", "xnor"),
        ("xor", "xor"),
        ("nand", "nand"),
        ("nor", "nor"),
        ("flip-flops", "dff"),
        ("flip-flop", "dff"),
        ("flip flops", "dff"),
        ("flip flop", "dff"),
        ("flipflop", "dff"),
        ("registers", "dff"),
        ("register", "dff"),
        ("dffs", "dff"),
        ("dff", "dff"),
        ("buffer", "buf"),
        ("buffers", "buf"),
        ("buf", "buf"),
        ("not", "not"),
        ("inv", "not"),
        ("inverter", "not"),
        ("and", "and"),
        ("or", "or"),
    ]
    for word, gate_type in aliases:
        if re.search(rf"\b{re.escape(word)}\b", low):
            return gate_type
    return None


def _extract_gate_type_before_count(low: str) -> str | None:
    aliases = {
        "and": "and",
        "or": "or",
        "not": "not",
        "nand": "nand",
        "nor": "nor",
        "xor": "xor",
        "xnor": "xnor",
        "buf": "buf",
        "buffer": "buf",
        "dff": "dff",
    }
    match = re.search(
        r"\b(and|or|not|nand|nor|xor|xnor|buf|buffer|dff)\s+gates?\s+count\b",
        low,
    )
    if match:
        return aliases[match.group(1)]
    match = re.search(
        r"\b(and|or|not|nand|nor|xor|xnor|buf|buffer|dff)\s+gate\s+count\b",
        low,
    )
    if match:
        return aliases[match.group(1)]
    return None


def _mentions_gate_type(low: str, gate_type: str) -> bool:
    return re.search(rf"\b{re.escape(gate_type)}\b", low) is not None


def _mentions_and_not_gate_set(low: str) -> bool:
    return bool(
        re.search(r"\band\s*(?:/|,|\+|and)\s*not\s+gates?\b", low)
        or re.search(r"\bnot\s*(?:/|,|\+|and)\s*and\s+gates?\b", low)
        or "and/not" in low
        or "and-not" in low
    )


def _extract_extra_input(text: str) -> str | None:
    patterns = [
        rf"(?:other|second|extra)\s+input\s+(?:to|as|is)?\s*({_SIGNAL_RE})",
        rf"connect\s+(?:the\s+)?(?:other|second|extra)?\s*input\s+to\s+({_SIGNAL_RE})",
        rf"\bwith\s+({_SIGNAL_RE})\s+as\s+(?:the\s+)?(?:other|second|extra)\s+input",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_instance_list(text: str) -> list[str]:
    quoted = _extract_quoted_text(text)
    if quoted and "," in quoted:
        return [_clean_token(token) for token in quoted.split(",") if _clean_token(token)]

    patterns = [
        r"(?:instances?|gates?|buffers?)\s+([A-Za-z0-9_$,\s]+)\s+(?:with|to|by|using)",
        r"(?:targets?)\s+([A-Za-z0-9_$,\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        raw = match.group(1)
        names = [_clean_token(token) for token in re.split(r"[,\s]+", raw)]
        return [name for name in names if name and name.lower() not in {"found", "the"}]

    return []


def _extract_src_dst(text: str) -> tuple[str, str] | None:
    src = _extract_after_keyword(text, "from")
    dst = _extract_after_keyword(text, "to")
    if src and dst:
        return src, dst
    patterns = [
        rf"\bbetween\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        rf"\bconnecting\s+(?:primary\s+)?input\s+({_SIGNAL_RE})\s+to\s+(?:primary\s+)?output\s+({_SIGNAL_RE})",
        rf"\boriginating\s+at\s+(?:primary\s+)?input\s+({_SIGNAL_RE}).*?\bterminating\s+at\s+(?:primary\s+)?output\s+({_SIGNAL_RE})",
        rf"\bfrom\s+(?:primary\s+)?input\s+({_SIGNAL_RE})\s+to\s+(?:primary\s+)?output\s+({_SIGNAL_RE})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1), match.group(2)
    return None


def _extract_after_keyword(text: str, keyword: str) -> str | None:
    pattern = rf"\b{re.escape(keyword)}\s+(?:(?:primary\s+)?(?:input|output)\s+|signal\s+|net\s+)?({_SIGNAL_RE})"
    match = re.search(pattern, text, flags=re.I)
    return match.group(1) if match else None


def _extract_avoid_list(text: str) -> list[str]:
    match = re.search(r"\bavoid(?:ing)?\s+([A-Za-z0-9_$,\s\[\]]+)", text, flags=re.I)
    if not match:
        match = re.search(r"\bdoes\s+not\s+traverse\s+(?:node\s+|signal\s+|net\s+)?([A-Za-z0-9_$\[\]]+)", text, flags=re.I)
    if not match:
        return []
    return [
        token
        for token in (_clean_token(part) for part in re.split(r"[,\s]+", match.group(1)))
        if token and token.lower() not in {"and", "or"}
    ]


def _extract_destination_list(text: str) -> list[str]:
    patterns = [
        rf"(?:dsts?|destinations?|outputs?)\s+((?:{_SIGNAL_RE}[\s,]*(?:and\s+)?){{1,}})",
        rf"\bto\s+((?:{_SIGNAL_RE}[\s,]*(?:and\s+)?){{1,}})(?:\s+with|\s+using|\s+by|\s+so|\s*$|[.])",
    ]
    stop_words = {"and", "with", "using", "by", "so", "buffer", "buffers"}
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        tokens = [
            token
            for token in (_clean_token(part) for part in re.split(r"[,\s]+", match.group(1)))
            if token and token.lower() not in stop_words
        ]
        if tokens:
            return tokens
    return []


def _extract_reconnect_request(text: str, low: str) -> tuple[str, str, str] | None:
    if "reconnect" not in low and "connect input pin" not in low:
        return None
    match = re.search(
        rf"\b(?:reconnect|connect)\s+(?:input\s+)?pin\s+([A-Za-z0-9_]+)\s+of\s+gate\s+({_SIGNAL_RE})\s+to\s+(?:internal\s+)?(?:signal\s+|net\s+|wire\s+)?({_SIGNAL_RE})",
        text,
        flags=re.I,
    )
    if match:
        return match.group(2), match.group(1), match.group(3)
    return None


def _extract_symmetry_request(text: str) -> tuple[str, str, str] | None:
    target = _extract_after_keyword(text, "at") or _extract_after_keyword(text, "function at")
    target = target or _extract_after_keyword(text, "target") or _extract_after_keyword(text, "output")
    match = re.search(
        rf"\bwith\s+respect\s+to\s+inputs?\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        text,
        flags=re.I,
    )
    if target and match:
        return target, match.group(1), match.group(2)
    return None


def _extract_dff_pair(text: str) -> tuple[str, str] | None:
    tokens = re.findall(_SIGNAL_RE, text)
    stop_words = {
        "does",
        "do",
        "are",
        "is",
        "and",
        "under",
        "same",
        "clock",
        "domain",
        "domains",
        "flip",
        "flop",
        "flipflop",
        "dff",
        "dffs",
    }
    candidates = [
        token
        for token in tokens
        if token.lower() not in stop_words and re.search(r"(?:^|_)d?ff|dff|\bff", token, flags=re.I)
    ]
    if len(candidates) >= 2:
        return candidates[0], candidates[1]
    return None


def _extract_equivalence(text: str) -> tuple[str, str] | None:
    quoted_expr = _extract_quoted_text(text)
    target = _extract_after_keyword(text, "to") or _extract_after_keyword(text, "target")
    if quoted_expr and target:
        return quoted_expr, target

    match = re.search(
        rf"(?:is|whether|verify|check|such\s+that)\s*(.+?)\s+"
        rf"(?:is\s+)?equivalent\s+to\s+(?:signal\s+|net\s+)?({_SIGNAL_RE})",
        text,
        flags=re.I,
    )
    if match:
        expr = _normalize_boolean_expr(match.group(1))
        return expr, match.group(2)

    match = re.search(
        rf"(?:is|whether|verify|check)\s+(?:signal\s+|net\s+)?({_SIGNAL_RE})\s+"
        rf"(?:is\s+)?equivalent\s+to\s+(.+)",
        text,
        flags=re.I,
    )
    if match:
        return _normalize_boolean_expr(match.group(2)), match.group(1)
    return None


def _extract_property(text: str) -> tuple[str, str] | None:
    match = re.search(
        rf"(?:for\s+)?(?:output\s+|signal\s+|net\s+)?({_SIGNAL_RE}).*?"
        rf"asserted\s+only\s+when\s+(.+)",
        text,
        flags=re.I,
    )
    if match:
        target = match.group(1)
        condition = _normalize_boolean_expr(match.group(2))
        return target, f"{target} -> ({condition})"

    quoted = _extract_quoted_text(text)
    target = _extract_after_keyword(text, "target") or _extract_after_keyword(text, "for")
    if quoted and target:
        return target, quoted
    return None


def _extract_connection_instance(text: str) -> str | None:
    patterns = [
        rf"\b(?:gate|instance|dff)\s+is\s+({_SIGNAL_RE})\b",
        rf"\bof\s+(?:gate|instance|dff)\s+({_SIGNAL_RE})",
        rf"\b(?:gate|instance|dff)\s+({_SIGNAL_RE})\b",
    ]
    ignored = {"type", "pin", "pins", "connection", "connections", "is"}
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match and match.group(1).lower() not in ignored:
            return match.group(1)
    return None


def _extract_signal_equivalence_pair(text: str) -> tuple[str, str] | None:
    match = re.search(
        rf"\b(?:determine\s+whether\s+)?signals?\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})\s+"
        rf"(?:are\s+)?functionally\s+equivalent",
        text,
        flags=re.I,
    )
    if match:
        return match.group(1), match.group(2)

    match = re.search(
        rf"\b(?:internal\s+)?signals?\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})\s+"
        rf"(?:are\s+)?functionally\s+equivalent",
        text,
        flags=re.I,
    )
    if match:
        return match.group(1), match.group(2)

    match = re.search(
        rf"\bequivalence\s+between\s+(?:internal\s+)?signals?\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        text,
        flags=re.I,
    )
    if match:
        return match.group(1), match.group(2)

    match = re.search(
        rf"\b(?:check|verify)\s+functional\s+equivalence\s+between\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        text,
        flags=re.I,
    )
    if match:
        return match.group(1), match.group(2)
    return None


def _extract_signal_pair(text: str) -> tuple[str, str] | None:
    match = re.search(rf"\b({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})\b", text, flags=re.I)
    if match:
        return match.group(1), match.group(2)
    return None


def _extract_targets_after_between_or_of(text: str) -> tuple[str, str] | None:
    patterns = [
        rf"\bbetween\s+(?:the\s+)?(?:fanin\s+)?(?:cones?\s+of\s+)?({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        rf"\bcones?\s+of\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
        rf"\bof\s+({_SIGNAL_RE})\s+and\s+({_SIGNAL_RE})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1), match.group(2)
    return None


def _extract_rename_net_pair(text: str, low: str) -> tuple[str, str] | None:
    if (
        not any(word in low for word in ("rename", "renaming"))
        and "update name" not in low
        and "update the name" not in low
        and "change the identifier" not in low
    ):
        return None
    if not any(word in low for word in ("net", "wire", "signal")):
        return None

    patterns = [
        rf"(?:rename|renaming)\s+(?:internal\s+)?(?:net|wire|signal)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"(?:rename|renaming)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"update\s+the\s+name\s+of\s+(?:internal\s+)?(?:net|wire|signal)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"update\s+name\s+of\s+(?:internal\s+)?(?:net|wire|signal)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"change\s+the\s+identifier\s+of\s+(?:internal\s+)?(?:net|wire|signal)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1), match.group(2)
    return None


def _extract_rename_gate_pair(text: str, low: str) -> tuple[str, str] | None:
    if (
        not any(word in low for word in ("rename", "renaming"))
        and "update name" not in low
        and "change the identifier" not in low
    ):
        return None
    if not any(word in low for word in ("gate", "instance", "dff", "flip-flop", "flip flop", "cell")):
        return None

    patterns = [
        rf"(?:rename|renaming)\s+(?:gate|instance|dff|flip-flop|flip\s+flop|cell)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"update\s+name\s+of\s+(?:gate|instance|dff|flip-flop|flip\s+flop|cell)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
        rf"change\s+the\s+identifier\s+of\s+(?:gate|instance|dff|flip-flop|flip\s+flop|cell)\s+({_SIGNAL_RE})\s+(?:to|as)\s+({_SIGNAL_RE})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1), match.group(2)
    return None


def _normalize_boolean_expr(text: str) -> str:
    expr = text.strip().strip("?.")
    expr = re.sub(r"^(?:whether|that)\s+", "", expr, flags=re.I)
    expr = re.sub(rf"\b({_SIGNAL_RE})\s+is\s+1\b", r"\1", expr, flags=re.I)
    expr = re.sub(rf"\b({_SIGNAL_RE})\s+is\s+0\b", r"!\1", expr, flags=re.I)
    expr = re.sub(r"\bboth\b", "", expr, flags=re.I)
    expr = re.sub(r"\band\b", "&", expr, flags=re.I)
    expr = re.sub(r"\bor\b", "|", expr, flags=re.I)
    expr = re.sub(r"\bnot\b", "!", expr, flags=re.I)
    expr = re.sub(r"\bis\s+equivalent\s+to\b", "", expr, flags=re.I)
    expr = expr.replace("&&", "&").replace("||", "|")
    expr = re.sub(r"\s+", " ", expr)
    return expr.strip()


def _extract_int_after(text: str, keyword: str) -> int | None:
    match = re.search(rf"\b{re.escape(keyword)}\b\D*(\d+)", text, flags=re.I)
    return int(match.group(1)) if match else None


def _extract_limit_int(text: str) -> int | None:
    patterns = [
        r"(?:<=|<|=)\s*(\d+)",
        r"\b(?:max(?:imum)?|limit|bound|threshold|at\s+most|no\s+more\s+than|less\s+than|under)\D+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))
    return _extract_last_int(text)


def _extract_path_limit_int(text: str) -> int | None:
    patterns = [
        r"\b(?:first|show|list|enumerate|report|limit(?:ed)?\s+to|up\s+to|at\s+most|no\s+more\s+than)\s+(\d+)\s+(?:paths?|path\b)",
        r"\b(?:paths?|path)\s*(?:<=|<|=)\s*(\d+)",
        r"\b(?:max(?:imum)?|limit|bound)\s+(?:number\s+of\s+)?paths?\D+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def _extract_first_int(text: str) -> int | None:
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else None


def _extract_last_int(text: str) -> int | None:
    matches = re.findall(r"\b(\d+)\b", text)
    return int(matches[-1]) if matches else None


def _clean_token(token: str) -> str:
    return token.strip().strip(".,;:()[]{}'\"")
