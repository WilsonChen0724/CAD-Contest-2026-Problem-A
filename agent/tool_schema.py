from __future__ import annotations

from typing import Any


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
    "report_outputs_by_cone_size",
    "same_clock_domain",
}

TRANSFORM_OPS = {
    "replace_buffers_with_and",
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "replace_or_with_nand_not",
    "insert_buffers_for_fanout",
    "balance_depth_with_buffers",
    "optimize_cone",
}

VERIFY_OPS = {
    "check_connectivity",
    "check_fanout",
    "check_depth",
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
    "report_outputs_by_cone_size": "Report primary outputs whose fanin cones exceed a gate-count threshold.",
    "same_clock_domain": "Check whether two DFF instances use the same clock net.",
    "replace_buffers_with_and": "Replace selected BUF gates with equivalent two-input AND gates using an extra control input.",
    "remove_dangling": "Remove gates, DFFs, and internal nets that do not contribute to any primary output.",
    "replace_inv_buf_with_inv": "Collapse safe inverter-buffer chains into a single inverter.",
    "replace_or_with_nand_not": "Rewrite two-input OR gates in a target cone as equivalent NAND/NOT logic.",
    "insert_buffers_for_fanout": "Insert buffers on a net so driven gate fanout is at most max_fanout.",
    "balance_depth_with_buffers": "Insert buffers to equalize logic depths from one source to several destinations.",
    "optimize_cone": "Run conservative local cone optimization under optional depth and gate-count constraints.",
    "check_connectivity": "Check missing drivers, duplicate drivers, and connectivity consistency.",
    "check_fanout": "Check whether all fanouts are within a maximum fanout bound.",
    "check_depth": "Check whether src-to-dst combinational depth is within a maximum bound.",
    "check_equivalence": "Check whether a Boolean expression is equivalent to a target signal.",
    "check_property": "Check whether a Boolean property holds for a target signal.",
    "unsupported": "Use only when the request cannot be mapped to any supported EDA operation.",
}

_STRING = {"type": "string"}
_STRING_ARRAY = {"type": "array", "items": _STRING}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_NULLABLE_STRING = {"type": ["string", "null"]}


def _args_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


OP_ARG_SCHEMAS: dict[str, dict[str, Any]] = {
    "begin_testcase": _args_schema({"case_name": _STRING}, ["case_name"]),
    "read_design": _args_schema({"path": _STRING}, ["path"]),
    "write_design": _args_schema({"path": _STRING}, ["path"]),
    "find_path": _args_schema(
        {
            "src": _STRING,
            "dst": _STRING,
            "avoid": {
                **_STRING_ARRAY,
                "description": "Optional gate or net names to avoid. Use this exact field name; do not use avoid_nodes.",
            },
        },
        ["src", "dst"],
    ),
    "all_paths_pass_through": _args_schema(
        {"src": _STRING, "dst": _STRING, "node": _STRING},
        ["src", "dst", "node"],
    ),
    "max_depth": _args_schema({"src": _STRING, "dst": _STRING}, ["src", "dst"]),
    "logic_cone": _args_schema({"target": _STRING}, ["target"]),
    "report_outputs_by_cone_size": _args_schema({"min_gates": _INT}, ["min_gates"]),
    "find_gates": _args_schema({"gate_type": _NULLABLE_STRING, "name_contains": _NULLABLE_STRING}, []),
    "same_clock_domain": _args_schema({"dff_a": _STRING, "dff_b": _STRING}, ["dff_a", "dff_b"]),
    "replace_buffers_with_and": _args_schema(
        {"targets": _STRING_ARRAY, "targets_from": _STRING, "extra_input": _STRING},
        ["extra_input"],
    ),
    "remove_dangling": _args_schema({}, []),
    "replace_inv_buf_with_inv": _args_schema({}, []),
    "replace_or_with_nand_not": _args_schema({"cone_target": _STRING}, ["cone_target"]),
    "insert_buffers_for_fanout": _args_schema({"net": _STRING, "max_fanout": _INT}, ["net", "max_fanout"]),
    "balance_depth_with_buffers": _args_schema(
        {"src": _STRING, "dsts": _STRING_ARRAY, "minimize_buffers": _BOOL},
        ["src", "dsts"],
    ),
    "optimize_cone": _args_schema(
        {"target": _STRING, "max_depth": _INT, "minimize_gate_count": _BOOL},
        ["target"],
    ),
    "check_connectivity": _args_schema({}, []),
    "check_fanout": _args_schema({"max_fanout": _INT}, ["max_fanout"]),
    "check_depth": _args_schema(
        {"src": _STRING, "dst": _STRING, "max_depth": _INT},
        ["src", "dst", "max_depth"],
    ),
    "check_equivalence": _args_schema({"expr": _STRING, "target": _STRING}, ["expr", "target"]),
    "check_property": _args_schema({"target": _STRING, "property": _STRING}, ["target", "property"]),
    "unsupported": _args_schema({"reason": _STRING}, ["reason"]),
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
            "Run read-only EDA analysis operations. Use for path, depth, cone, fanout, gate search, cone-size, and clock-domain questions. This tool must not modify the design.",
            sorted(ANALYSIS_OPS | {"unsupported"}),
        ),
        _tool(
            "run_transform_plan",
            "Run design-modifying transformations. Use for buffer insertion, dangling removal, gate rewrites, depth balancing, and cone optimization. Prefer function-preserving operations when requested.",
            sorted(TRANSFORM_OPS | {"unsupported"}),
        ),
        _tool(
            "run_verify_plan",
            "Run verification and safety checks. Use for connectivity, fanout/depth bounds, Boolean equivalence, and property checks.",
            sorted(VERIFY_OPS | {"unsupported"}),
        ),
    ]


def _tool(name: str, description: str, ops: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": False,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "One or more backend EDA operations to execute in order.",
                    "minItems": 1,
                    "items": {"anyOf": [_step_schema(op) for op in ops]},
                }
            },
            "required": ["steps"],
        },
    }


def _step_schema(op: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "op": {
                "type": "string",
                "enum": [op],
                "description": OP_DESCRIPTIONS[op],
            },
            "args": OP_ARG_SCHEMAS[op],
            "save_as": {
                "type": ["string", "null"],
                "description": "Optional key for saving this step result for later steps. Use null when not needed.",
            },
        },
        "required": ["op", "args", "save_as"],
    }
