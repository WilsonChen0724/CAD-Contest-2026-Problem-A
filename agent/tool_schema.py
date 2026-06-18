from __future__ import annotations

from typing import Any


# Schema design note:
#
# The current OpenAI domain tools intentionally use a generic
# `args: object` schema for each step. This keeps tool-schema maintenance light
# while backend operations are still changing: adding or editing an EDA op only
# requires updating the op lists here plus the hard validation rules in
# `agent/plan_checker.py`.
#
# Tradeoff:
# - Generic args object:
#   - Faster to iterate and easier to keep in sync during development.
#   - OpenAI validates the selected domain tool and op enum, but op-specific
#     mistakes such as `{"op": "max_depth", "args": {"path": "x.v"}}` are
#     caught later by the local plan checker.
# - Per-op `anyOf` variants:
#   - More stable for the LLM because each op carries its exact required and
#     optional args schema.
#   - Higher maintenance cost because every backend op needs a matching schema
#     variant, and schema/docs/checker/dispatcher must stay synchronized.
#
# Recommended path:
# keep this generic schema during rapid tool development; once the operation
# set stabilizes before submission, tighten each domain tool into per-op
# `anyOf` variants. In both versions, `plan_checker.py` remains the required
# local safety boundary.


IO_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
}

ANALYSIS_OPS = {
    "find_gates",
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
    "report_gate_type_connections",
    "report_primary_inputs",
    "report_primary_outputs",
    "report_fanout",
    "report_highest_fanout_primary_input",
    "report_gate_connections",
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
    "same_clock_domain",
    "report_dffs_by_clock",
    "check_equivalence",
    "check_signal_symmetry",
}

TRANSFORM_OPS = {
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
}

VERIFY_OPS = {
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalent_to_original",
    "check_equivalent_to_last_transform_input",
    "check_equivalence",
    "check_property",
    "check_signal_symmetry",
}

TOOL_ALLOWED_OPS = {
    "run_design_io_plan": IO_OPS | {"unsupported"},
    "run_analysis_plan": ANALYSIS_OPS | {"unsupported"},
    "run_transform_plan": TRANSFORM_OPS | ANALYSIS_OPS | VERIFY_OPS | {"unsupported"},
    "run_verify_plan": VERIFY_OPS | {"report_last_transform_stats", "unsupported"},
}
OP_DESCRIPTIONS = {
    "begin_testcase": "Initialize a new testcase and reset testcase-local design state.",
    "read_design": "Read a gate-level Verilog .v file into the current design state.",
    "write_design": "Write the current design state to a gate-level Verilog .v output file.",
    "find_gates": "Find gates by type and/or name substring, such as buffers named _gc__.",
    "find_path": "Find one combinational path from src to dst, optionally avoiding named gates or nets.",
    "all_paths_pass_through": "Check whether every combinational path from src to dst passes through one node.",
    "report_all_paths": "List bounded combinational paths from src to dst. Args: src, dst; optional max_paths.",
    "all_paths": "List capped combinational paths from src to dst. Args: src, dst; optional max_paths.",
    "report_direct_pi_po_paths": "List zero-gate direct wire connections where a primary input is also a primary output.",
    "check_cut_signal": "Check whether a signal is a structural cut on at least one PI-to-PO path. Args: signal.",
    "max_depth": "Compute maximum combinational gate depth from src to dst.",
    "report_max_logic_depth": "Report the maximum combinational logic depth in the whole design.",
    "report_cone_depth": "Report maximum structural depth and an example path inside one target fanin cone. Args: target.",
    "logic_cone": "Report the transitive fanin cone of a target net or primary output, including the total number of gates in that cone.",
    "report_gate_counts": "Report total gate counts broken down by primitive gate type.",
    "report_gate_type_count": "Report the current count for one gate type such as not, nand, buf, or dff. Args: gate_type.",
    "report_gate_type_connections": "List gates of one type with their input and output signals. Args: gate_type; optional max_items.",
    "report_primary_inputs": "List primary input names grouped with inferred bit widths.",
    "report_primary_outputs": "List primary output names grouped with inferred bit widths.",
    "report_fanout": "Report direct loads driven by a net, gate instance output, or DFF Q output. Args: net.",
    "report_highest_fanout_primary_input": "Report which primary input has the largest direct fanout. Args: none.",
    "report_gate_connections": "Report one gate or DFF instance's type, pins, output net, and output fanout. Args: gate.",
    "report_gates_by_type": "List gates of a given primitive type with input and output signals. Args: gate_type such as nand, nor, dff; optional limit.",
    "report_gate_type_count_in_cone": "Count gates of one explicit primitive type in one target fanin cone. Use only for questions like how many NAND gates are in the cone of output n8. For total gates in a cone, use logic_cone instead. Args: target, gate_type.",
    "report_deepest_output_cone": "Report output bit(s) with the deepest fanin logic depth.",
    "report_largest_fanin_cone_output": "Report primary output bit(s) with the largest structural fanin cone by gate count.",
    "report_outputs_by_cone_size": "Report primary outputs whose fanin cones exceed a gate-count threshold.",
    "report_fanout_cone": "Report gates and nets transitively reachable from a source net, primary input, or DFF Q output. Args: source.",
    "report_constant_input_gates": "Report gates with constant input pins, optionally filtered by gate_type such as nand.",
    "report_io_counts": "Report the number of primary inputs and primary outputs.",
    "gate_on_max_depth_path": "Check whether a gate lies on any global maximum-depth combinational path. Args: gate.",
    "report_articulation_points": "Report combinational graph articulation points between source and destination nodes. Args: src, dst.",
    "report_shared_fanin_cone_gates": "Report gates shared by the fanin cones of two targets. Args: target_a, target_b.",
    "derive_boolean_equation": "Derive a structural Boolean equation for a target when tractable. Args: target.",
    "find_nand_equivalent_pair": "Check whether a bounded pair of existing internal signals a,b has NAND(a,b) equivalent to a target. Args: target.",
    "report_max_depth_to_dff_d": "Report maximum combinational depth from any primary input to any DFF D pin.",
    "report_max_register_to_register_depth": "Report maximum combinational depth from any DFF Q pin to any downstream DFF D pin.",
    "report_outputs_depth_greater_than": "Report primary outputs whose structural logic depth is greater than min_depth. Args: min_depth.",
    "report_register_paths": "Report capped structural DFF Q to downstream DFF D combinational paths. Optional args: max_paths.",
    "report_dff_input_logic_structures": "Report DFF D-pin logic that structurally resembles enable/hold logic through mux-like or AND-gate patterns.",
    "report_last_transform_stats": "Report delta stats from the previous successful transform, including gate type, inserted/removed gate, net, and DFF changes.",
    "same_clock_domain": "Check whether two DFF instances use the same clock net.",
    "report_dffs_by_clock": "List flip-flops whose CLK pin is driven by a clock net. Use for 'flip-flops driven by clock n0'. Args: clock.",
    "replace_buffers_with_and": "Replace selected BUF gates with equivalent two-input AND gates using an extra control input.",
    "remove_dangling": "Remove gates, DFFs, and internal nets that do not contribute to any primary output.",
    "replace_inv_buf_with_inv": "Collapse safe inverter-buffer chains into a single inverter.",
    "collapse_back_to_back_inverters": "Collapse safe NOT followed by NOT chains into direct wiring while preserving function.",
    "replace_or_with_nand_not": "Rewrite two-input OR gates in a target cone as equivalent NAND/NOT logic. Use for restructuring a cone using NAND and NOT gates.",
    "replace_nand_const1_with_not": "Replace NAND gates with one input tied to 1'b1 by equivalent NOT gates.",
    "replace_with_and_not": "Reconstruct primitive combinational gates using only AND and NOT gates while preserving Boolean function. Use before depth optimization when the task requires an AND/NOT-only final design. Args: none.",
    "insert_buffers_for_fanout": "Insert buffers on a net so driven gate fanout is at most max_fanout.",
    "insert_dedicated_buffers_for_each_load": "Insert one dedicated BUF per current load of a net or signal. Args: net.",
    "insert_buffers_for_all_high_fanout": "Insert buffers on every currently high-fanout net so driven gate fanout is at most max_fanout. Args: max_fanout.",
    "balance_depth_with_buffers": "Insert buffers to equalize logic depths from one source to several destinations.",
    "optimize_cone": "Run conservative local cone optimization. Args: target; optional max_depth, minimize_gate_count, allowed_gates, objective. When a prompt requires a target cone to contain only certain gate types, pass allowed_gates exactly, for example [\"and\", \"or\", \"not\"] or [\"nor\", \"not\"].",
    "constant_propagation": "Simplify gates with constant or redundant inputs while preserving behavior. Args: none.",
    "optimize_design_depth": "Run conservative design-depth optimization. Args: optional max_depth, cost_function, cost_scope, allowed_gates, constraints, objective. When the prompt says final design maximum logic depth or says smaller logic depth is better, use cost_function=\"max_logic_depth\", objective=\"minimize\", and cost_scope=\"whole_design\". If the prompt says the final/whole design must remain in a gate library such as AND/NOT only, pass top-level allowed_gates, e.g. [\"and\", \"not\"]. Put only named-signal cone gate-library limits in constraints, e.g. [{\"type\":\"cone_gate_library\",\"target\":\"n10\",\"allowed_gates\":[\"nor\",\"not\"]}]. Never use target \"whole_design\", \"design\", or \"netlist\" in a cone_gate_library constraint.",
    "replace_xnor_nor_with_basic_gates": "Backward-compatible alias for replacing XNOR gates with equivalent NOR-only logic.",
    "replace_xnor_with_nor": "Replace all 2-input XNOR gates with equivalent NOR-only implementations.",
    "replace_xor_with_nand": "Replace all 2-input XOR gates with equivalent NAND-only implementations.",
    "replace_and_not_with_nand": "Rewrite AND and NOT gates into equivalent NAND-only structures.",
    "merge_equivalent_gates": "Merge structurally identical primitive gates when the duplicate output is internal.",
    "rename_gate": "Rename one gate or DFF instance without changing connectivity. Args: old_name, new_name.",
    "rename_net": "Rename one net safely by updating declarations and all structural references. Args: old_net, new_net.",
    "reconnect_gate_input": "Try to reconnect one primitive gate input pin while preserving functionality. Args: gate, pin, new_net.",
    "check_connectivity": "Check missing drivers, duplicate drivers, and connectivity consistency.",
    "check_fanout": "Check whether all fanouts are within a maximum fanout bound.",
    "check_depth": "Check whether src-to-dst combinational depth is within a maximum bound.",
    "check_equivalent_to_original": "Check whether the current design is equivalent to the original netlist snapshot captured by read_design. Args: none.",
    "check_equivalent_to_last_transform_input": "Check whether the current design is equivalent to the netlist state immediately before the previous successful transform.",
    "check_equivalence": "Check whether a Boolean expression is equivalent to a target signal. Use expr \"0\" or \"1\" for always-0 or always-1 questions.",
    "check_property": "Check whether a Boolean property holds for a target signal.",
    "check_signal_symmetry": "Check whether a target function is symmetric when two inputs are swapped. Use directly for 'function at X is symmetric with respect to inputs A and B'. Args: target, input_a, input_b.",
    "unsupported": "Use only when the request cannot be mapped to any supported EDA operation.",
}


def openai_domain_tools() -> list[dict[str, Any]]:
    return [
        _tool(
            "run_design_io_plan",
            "Run testcase lifecycle and Verilog file IO operations. Use for beginning a testcase, reading a design, and writing an output netlist.",
            sorted(IO_OPS | {"unsupported"}),
        ),
        _tool(
            "run_analysis_plan",
            "Run read-only EDA analysis operations. Use for path, depth, cone, fanout, gate search, cone-size, register path, and clock-domain questions. This tool must not modify the design.",
            sorted(ANALYSIS_OPS | {"unsupported"}),
        ),
        _tool(
            "run_transform_plan",
            "Run design-modifying transformations and immediate read-only follow-up reports/checks. Use for buffer insertion, dangling removal, gate rewrites, depth balancing, constant propagation, fanout optimization, equivalent-gate merge, and cone/design-depth optimization. Prefer function-preserving operations when requested.",
            sorted(TRANSFORM_OPS | ANALYSIS_OPS | VERIFY_OPS | {"unsupported"}),
        ),
        _tool(
            "run_verify_plan",
            "Run verification and safety checks. Use for connectivity, fanout/depth bounds, Boolean equivalence, and property checks.",
            sorted(VERIFY_OPS | {"unsupported"}),
        ),
    ]


def anthropic_domain_tools() -> list[dict[str, Any]]:
    """Return the same domain tools in Anthropic Messages API format."""
    tools: list[dict[str, Any]] = []
    for tool in openai_domain_tools():
        tools.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
            }
        )
    return tools


def _tool(name: str, description: str, ops: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        # Keep this non-strict until each operation has its own exact args
        # schema. The local plan checker remains the hard safety boundary.
        "strict": False,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "One or more backend EDA operations to execute in order.",
                    "minItems": 1,
                    "items": _step_schema(ops),
                }
            },
            "required": ["steps"],
        },
    }


def _step_schema(ops: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "op": {
                "type": "string",
                "enum": ops,
                "description": _operation_descriptions(ops),
            },
            "args": {
                "type": "object",
                "description": "Arguments for the selected operation. Use the exact arg names from the selected op description, and preserve gate, signal, and file names exactly as written by the user.",
                "additionalProperties": True,
            },
            "save_as": {
                "type": ["string", "null"],
                "description": "Optional key for saving this step result for later steps. Use null when not needed.",
            },
        },
        "required": ["op", "args", "save_as"],
    }


def _operation_descriptions(ops: list[str]) -> str:
    return "Available operations: " + " ".join(
        f"{op}: {OP_DESCRIPTIONS[op]}" for op in ops
    )



