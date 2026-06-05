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
    "max_depth",
    "logic_cone",
    "report_gate_counts",
    "report_fanout",
    "report_gate_connections",
    "report_outputs_by_cone_size",
    "report_register_paths",
    "same_clock_domain",
}

TRANSFORM_OPS = {
    "replace_buffers_with_and",
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "replace_or_with_nand_not",
    "insert_buffers_for_fanout",
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
}

VERIFY_OPS = {
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "check_equivalent_to_original",
    "check_equivalence",
    "check_property",
}

TOOL_ALLOWED_OPS = {
    "run_design_io_plan": IO_OPS | {"unsupported"},
    "run_analysis_plan": ANALYSIS_OPS | {"unsupported"},
    "run_transform_plan": TRANSFORM_OPS | {"unsupported"},
    "run_verify_plan": VERIFY_OPS | {"unsupported"},
}
OP_DESCRIPTIONS = {
    "begin_testcase": "Initialize a new testcase and reset testcase-local design state.",
    "read_design": "Read a gate-level Verilog .v file into the current design state.",
    "write_design": "Write the current design state to a gate-level Verilog .v output file.",
    "find_gates": "Find gates by type and/or name substring, such as buffers named _gc__.",
    "find_path": "Find one combinational path from src to dst, optionally avoiding named gates or nets.",
    "all_paths_pass_through": "Check whether every combinational path from src to dst passes through one node.",
    "max_depth": "Compute maximum combinational gate depth from src to dst.",
    "logic_cone": "Report the transitive fanin cone of a target net or primary output.",
    "report_gate_counts": "Report total gate counts broken down by primitive gate type.",
    "report_fanout": "Report direct loads driven by a net, gate instance output, or DFF Q output. Args: net.",
    "report_gate_connections": "Report one gate or DFF instance's type, pins, output net, and output fanout. Args: gate.",
    "report_outputs_by_cone_size": "Report primary outputs whose fanin cones exceed a gate-count threshold.",
    "report_register_paths": "Report structural register-to-register, PI-to-register, and register-to-output paths.",
    "same_clock_domain": "Check whether two DFF instances use the same clock net.",
    "replace_buffers_with_and": "Replace selected BUF gates with equivalent two-input AND gates using an extra control input.",
    "remove_dangling": "Remove gates, DFFs, and internal nets that do not contribute to any primary output.",
    "replace_inv_buf_with_inv": "Collapse safe inverter-buffer chains into a single inverter.",
    "replace_or_with_nand_not": "Rewrite two-input OR gates in a target cone as equivalent NAND/NOT logic.",
    "insert_buffers_for_fanout": "Insert buffers on a net so driven gate fanout is at most max_fanout.",
    "insert_buffers_for_all_high_fanout": "Insert buffers on every currently high-fanout net so driven gate fanout is at most max_fanout. Args: max_fanout.",
    "balance_depth_with_buffers": "Insert buffers to equalize logic depths from one source to several destinations.",
    "optimize_cone": "Run conservative local cone optimization under optional depth and gate-count constraints.",
    "constant_propagation": "Simplify gates with constant or redundant inputs while preserving behavior. Args: none.",
    "optimize_design_depth": "Run conservative local cone optimization over primary outputs, optionally bounded by max_depth.",
    "replace_xnor_nor_with_basic_gates": "Rewrite XNOR/NOR gates into equivalent XOR/OR plus NOT structures.",
    "replace_and_not_with_nand": "Rewrite AND and NOT gates into equivalent NAND-only structures.",
    "merge_equivalent_gates": "Merge structurally identical primitive gates when the duplicate output is internal.",
    "rename_gate": "Rename one gate or DFF instance without changing connectivity. Args: old_name, new_name.",
    "rename_net": "Rename one net safely by updating declarations and all structural references. Args: old_net, new_net.",
    "check_connectivity": "Check missing drivers, duplicate drivers, and connectivity consistency.",
    "check_fanout": "Check whether all fanouts are within a maximum fanout bound.",
    "check_depth": "Check whether src-to-dst combinational depth is within a maximum bound.",
    "check_equivalent_to_original": "Check whether the current design is equivalent to the original netlist snapshot captured by read_design. Args: none.",
    "check_equivalence": "Check whether a Boolean expression is equivalent to a target signal.",
    "check_property": "Check whether a Boolean property holds for a target signal.",
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
            "Run design-modifying transformations. Use for buffer insertion, dangling removal, gate rewrites, depth balancing, constant propagation, fanout optimization, equivalent-gate merge, and cone/design-depth optimization. Prefer function-preserving operations when requested.",
            sorted(TRANSFORM_OPS | {"unsupported"}),
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
