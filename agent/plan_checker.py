from __future__ import annotations

import json
from typing import Any

from agent.tool_schema import TOOL_ALLOWED_OPS


class PlanValidationError(ValueError):
    """Raised when an LLM plan is not safe to dispatch."""


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
    "unsupported",
}

_REQUIRED_ARGS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "begin_testcase": {"case_name": str},
    "read_design": {"path": str},
    "write_design": {"path": str},
    "find_path": {"src": str, "dst": str},
    "all_paths_pass_through": {"src": str, "dst": str, "node": str},
    "report_all_paths": {"src": str, "dst": str},
    "all_paths": {"src": str, "dst": str},
    "report_direct_pi_po_paths": {},
    "check_cut_signal": {"signal": str},
    "max_depth": {"src": str, "dst": str},
    "report_max_logic_depth": {},
    "report_cone_depth": {"target": str},
    "logic_cone": {"target": str},
    "report_gate_counts": {},
    "report_gate_type_count": {"gate_type": str},
    "report_primary_inputs": {},
    "report_primary_outputs": {},
    "report_fanout": {"net": str},
    "report_highest_fanout_primary_input": {},
    "report_gate_connections": {"gate": str},
    "report_gate_type_connections": {"gate_type": str},
    "report_gates_by_type": {"gate_type": str},
    "report_gate_type_count_in_cone": {"target": str, "gate_type": str},
    "report_deepest_output_cone": {},
    "report_largest_fanin_cone_output": {},
    "report_outputs_by_cone_size": {"min_gates": int},
    "report_fanout_cone": {"source": str},
    "report_constant_input_gates": {},
    "report_io_counts": {},
    "gate_on_max_depth_path": {"gate": str},
    "report_articulation_points": {"src": str, "dst": str},
    "report_shared_fanin_cone_gates": {"target_a": str, "target_b": str},
    "derive_boolean_equation": {"target": str},
    "find_nand_equivalent_pair": {"target": str},
    "report_max_depth_to_dff_d": {},
    "report_max_register_to_register_depth": {},
    "report_outputs_depth_greater_than": {"min_depth": int},
    "report_register_paths": {},
    "report_dff_input_logic_structures": {},
    "report_last_transform_stats": {},
    "find_gates": {},
    "same_clock_domain": {"dff_a": str, "dff_b": str},
    "report_dffs_by_clock": {"clock": str},
    "replace_buffers_with_and": {"extra_input": str},
    "remove_dangling": {},
    "replace_inv_buf_with_inv": {},
    "collapse_back_to_back_inverters": {},
    "replace_or_with_nand_not": {"cone_target": str},
    "replace_nand_const1_with_not": {},
    "replace_with_and_not": {},
    "insert_buffers_for_fanout": {"net": str, "max_fanout": int},
    "insert_dedicated_buffers_for_each_load": {"net": str},
    "insert_buffers_for_all_high_fanout": {"max_fanout": int},
    "balance_depth_with_buffers": {"src": str, "dsts": list},
    "optimize_cone": {"target": str},
    "constant_propagation": {},
    "optimize_design_depth": {},
    "replace_xnor_nor_with_basic_gates": {},
    "replace_xnor_with_nor": {},
    "replace_xor_with_nand": {},
    "replace_and_not_with_nand": {},
    "merge_equivalent_gates": {},
    "rename_gate": {"old_name": str, "new_name": str},
    "rename_net": {"old_net": str, "new_net": str},
    "reconnect_gate_input": {"gate": str, "pin": str, "new_net": str},
    "check_connectivity": {},
    "check_fanout": {"max_fanout": int},
    "check_depth": {"src": str, "dst": str, "max_depth": int},
    "check_equivalent_to_original": {},
    "check_equivalent_to_last_transform_input": {},
    "check_equivalence": {"expr": str, "target": str},
    "check_property": {"target": str, "property": str},
    "check_signal_symmetry": {"target": str, "input_a": str, "input_b": str},
    "unsupported": {"reason": str},
}

_OPTIONAL_ARGS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "find_path": {"avoid": list},
    "report_all_paths": {"max_paths": int},
    "all_paths": {"max_paths": int},
    "report_gate_type_connections": {"max_items": int},
    "report_dffs_by_clock": {"max_items": int},
    "report_dff_input_logic_structures": {"max_items": int},
    "find_gates": {"gate_type": (str, type(None)), "name_contains": (str, type(None))},
    "report_gates_by_type": {"limit": int},
    "report_constant_input_gates": {"gate_type": (str, type(None))},
    "report_register_paths": {"max_paths": int},
    "replace_buffers_with_and": {"targets": list, "targets_from": str},
    "balance_depth_with_buffers": {"minimize_buffers": bool},
    "optimize_cone": {"max_depth": int, "minimize_gate_count": bool, "allowed_gates": list, "objective": str},
    "optimize_design_depth": {"max_depth": int, "allowed_gates": list, "objective": str, "cost_function": str, "cost_scope": str, "constraints": list},
}

def parse_plan_json(raw_plan: str) -> dict[str, Any]:
    """
    Convert raw LLM text into a Python plan dict.

    Input:
        raw_plan:
            Text returned by the LLM. It must be one JSON object, not Markdown.

    Output:
        A validated Python dict that is safe to pass to runtime.dispatcher.

    Raises:
        PlanValidationError when the text is not JSON or does not match the
        supported Tool API shape.
    """
    try:
        plan = json.loads(raw_plan)
    except json.JSONDecodeError as exc:
        raise PlanValidationError(
            "The planner response was not valid JSON. "
            "Please return exactly one JSON object that matches docs/tool_spec.md."
        ) from exc

    return validate_plan(plan)


def validate_domain_tool_plan(tool_name: str, tool_args: Any) -> dict[str, Any]:
    """
    Validate arguments returned by one provider domain tool call.

    The provider tool schema keeps output structurally stable, but this function is the
    local safety boundary: it checks the called tool name, rejects operations
    outside that tool's category, and then reuses the per-op plan validation.
    """
    if tool_name not in TOOL_ALLOWED_OPS:
        raise PlanValidationError(f"Unexpected provider tool call '{tool_name}'.")
    if not isinstance(tool_args, dict):
        raise PlanValidationError(f"{tool_name}.arguments must be a JSON object.")

    unknown_keys = set(tool_args) - {"steps"}
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise PlanValidationError(f"{tool_name}.arguments has unknown field(s): {names}")

    steps = tool_args.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanValidationError(f"{tool_name}.arguments.steps must be a non-empty list.")

    steps = _drop_empty_tool_steps(steps)
    if not steps:
        raise PlanValidationError(f"{tool_name}.arguments.steps must contain at least one operation step.")

    repaired_steps = _repair_readonly_report_with_extra_transform_steps(tool_name, steps)
    if repaired_steps is not None:
        normalized_steps = [_normalize_tool_step(step) for step in repaired_steps]
        return validate_plan({"steps": normalized_steps})

    allowed_ops = TOOL_ALLOWED_OPS[tool_name]
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise PlanValidationError(f"{tool_name}.steps[{index}] must be a JSON object.")
        op = step.get("op")
        if op not in allowed_ops:
            raise PlanValidationError(
                f"Operation '{op}' is not allowed in provider tool '{tool_name}'."
            )

    normalized_steps = [_normalize_tool_step(step) for step in steps]
    normalized_steps = _drop_fanout_gate_count_depth_overplan(normalized_steps)
    normalized_steps = _drop_redundant_cone_library_followups(normalized_steps)
    return validate_plan({"steps": normalized_steps})


def _repair_readonly_report_with_extra_transform_steps(
    tool_name: str,
    steps: list[Any],
) -> list[Any] | None:
    """
    Recover common LLM over-planning for follow-up report prompts.

    Example: after a transform has already run, a prompt such as "Report the NOR
    count after replacing XNOR gates" should be a read-only count. Some LLMs
    include the previous transform again, sometimes under the wrong provider
    tool. When there is at least one valid analysis step, keep only the
    read-only analysis steps so the report can proceed without mutating the
    design.
    """
    if tool_name == "run_transform_plan":
        return None
    analysis_ops = TOOL_ALLOWED_OPS["run_analysis_plan"]
    transform_ops = TOOL_ALLOWED_OPS["run_transform_plan"] - {"unsupported"}
    has_analysis_step = any(
        isinstance(step, dict)
        and step.get("op") in analysis_ops
        and step.get("op") != "unsupported"
        for step in steps
    )
    has_extra_transform_step = any(
        isinstance(step, dict)
        and step.get("op") in transform_ops
        and step.get("op") not in analysis_ops
        for step in steps
    )
    if not has_analysis_step or not has_extra_transform_step:
        return None
    return [
        step
        for step in steps
        if not (
            isinstance(step, dict)
            and step.get("op") in transform_ops
            and step.get("op") not in analysis_ops
        )
    ]


def _drop_empty_tool_steps(steps: list[Any]) -> list[Any]:
    return [
        step
        for step in steps
        if not (
            isinstance(step, dict)
            and set(step) <= {"save_as"}
            and step.get("save_as") is None
        )
    ]


def _drop_fanout_gate_count_depth_overplan(steps: list[Any]) -> list[Any]:
    """
    Fanout-buffer prompts should stop after buffer insertion.

    LLMs sometimes append optimize_design_depth after a fanout-buffer step, but
    that backend optimizes logic depth and can trigger expensive full-design ABC.
    A request that needs both fanout buffering and depth optimization should be
    split across separate user prompts, so drop the extra step here.
    """
    has_fanout_buffer_step = any(
        isinstance(step, dict)
        and step.get("op") in {"insert_buffers_for_fanout", "insert_buffers_for_all_high_fanout"}
        for step in steps
    )
    if not has_fanout_buffer_step:
        return steps

    repaired: list[Any] = []
    for step in steps:
        if isinstance(step, dict) and step.get("op") == "optimize_design_depth":
            continue
        repaired.append(step)
    return repaired


def _drop_redundant_cone_library_followups(steps: list[Any]) -> list[Any]:
    """Keep a target-local gate-library request inside optimize_cone."""
    has_constrained_cone = any(
        isinstance(step, dict)
        and step.get("op") == "optimize_cone"
        and isinstance(step.get("args"), dict)
        and bool(step["args"].get("allowed_gates"))
        for step in steps
    )
    if not has_constrained_cone:
        return steps
    whole_design_library_rewrites = {"replace_with_and_not", "replace_and_not_with_nand"}
    return [
        step
        for step in steps
        if not (isinstance(step, dict) and step.get("op") in whole_design_library_rewrites)
    ]


def validate_plan(plan: Any) -> dict[str, Any]:
    """
    Validate one tool plan or one multi-step plan.

    Input:
        plan:
            Python object produced by json.loads() or by the rule-based planner.

    Output:
        The same plan dict after validation. The function does not mutate it.

    Checked rules:
        - Top-level value must be a dict.
        - Plan must be either {"op": ..., "args": ...} or {"steps": [...]}.
        - Every op must be supported by the current dispatcher.
        - args must be a dict with required fields and correct basic types.
        - save_as, when present, must be a string or null.
    """
    if not isinstance(plan, dict):
        raise PlanValidationError("Plan must be a JSON object.")

    if "steps" in plan:
        _reject_unknown_keys(plan, {"steps"})
        steps = plan["steps"]
        if not isinstance(steps, list) or not steps:
            raise PlanValidationError("steps must be a non-empty list.")
        normalized_steps = [_normalize_tool_step(step) if isinstance(step, dict) else step for step in steps]
        for index, step in enumerate(normalized_steps, start=1):
            try:
                _validate_single_step(step)
            except PlanValidationError as exc:
                raise PlanValidationError(f"Invalid step {index}: {exc}") from exc
        return {"steps": normalized_steps}

    normalized_plan = _normalize_tool_step(plan)
    _validate_single_step(normalized_plan)
    return normalized_plan


def is_unsupported_plan(plan: dict[str, Any]) -> bool:
    return "steps" not in plan and plan.get("op") == "unsupported"


def format_plan_error(error: Exception) -> str:
    """
    Convert a checker exception into a response suitable for stdout/logs.

    The detailed exception still helps developers debug the exact failed rule,
    while this wrapper gives users a stable and contest-friendly message.
    """
    return (
        "Tool call rejected by plan checker. "
        f"Reason: {error}. "
        "Please use one of the supported operations in docs/tool_spec.md."
    )


def _validate_single_step(step: Any) -> None:
    if not isinstance(step, dict):
        raise PlanValidationError("Each step must be a JSON object with op and args.")

    _reject_unknown_keys(step, {"op", "args", "save_as"})

    op = step.get("op")
    if not isinstance(op, str):
        raise PlanValidationError("Field 'op' must be a string.")
    if op not in SUPPORTED_OPS:
        raise PlanValidationError(f"Unsupported operation '{op}'.")

    args = step.get("args")
    if not isinstance(args, dict):
        raise PlanValidationError("Field 'args' must be a JSON object.")

    _normalize_optional_null_args(op, args)

    if "save_as" in step and step["save_as"] is not None and not isinstance(step["save_as"], str):
        raise PlanValidationError("Field 'save_as' must be a string or null.")

    _validate_args(op, args)


def _normalize_optional_null_args(op: str, args: dict[str, Any]) -> None:
    optional = _OPTIONAL_ARGS.get(op, {})
    for key in list(args):
        if key in optional and args[key] is None:
            del args[key]


def _validate_args(op: str, args: dict[str, Any]) -> None:
    required = _REQUIRED_ARGS[op]
    optional = _OPTIONAL_ARGS.get(op, {})
    allowed_keys = set(required) | set(optional)

    for key in required:
        if key not in args:
            raise PlanValidationError(f"Operation '{op}' is missing required argument '{key}'.")

    for key in args:
        if key not in allowed_keys:
            raise PlanValidationError(f"Operation '{op}' has unknown argument '{key}'.")

    for key, expected_type in {**required, **optional}.items():
        if key in args and not _matches_expected_type(args[key], expected_type):
            raise PlanValidationError(f"Operation '{op}' argument '{key}' has the wrong type.")

    if op == "replace_buffers_with_and":
        has_targets = "targets" in args
        has_targets_from = "targets_from" in args
        if has_targets == has_targets_from:
            raise PlanValidationError(
                "Operation 'replace_buffers_with_and' needs exactly one of "
                "'targets' or 'targets_from'."
            )
        if has_targets and not all(isinstance(item, str) for item in args["targets"]):
            raise PlanValidationError("replace_buffers_with_and.args.targets must be a list of strings.")

    if op == "find_path" and "avoid" in args:
        if not all(isinstance(item, str) for item in args["avoid"]):
            raise PlanValidationError("find_path.args.avoid must be a list of strings.")

    if op == "balance_depth_with_buffers":
        if not args["dsts"] or not all(isinstance(item, str) for item in args["dsts"]):
            raise PlanValidationError("balance_depth_with_buffers.args.dsts must be a non-empty list of strings.")

    _validate_non_empty_strings(op, args)
    _validate_numeric_bounds(op, args)


def _validate_non_empty_strings(op: str, args: dict[str, Any]) -> None:
    for key, value in args.items():
        if isinstance(value, str) and not value.strip():
            raise PlanValidationError(f"Operation '{op}' argument '{key}' must not be empty.")
        if isinstance(value, list) and any(isinstance(item, str) and not item.strip() for item in value):
            raise PlanValidationError(f"Operation '{op}' argument '{key}' must not contain empty strings.")


def _matches_expected_type(value: Any, expected_type: type | tuple[type, ...]) -> bool:
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(expected_type, tuple) and int in expected_type and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


def _validate_numeric_bounds(op: str, args: dict[str, Any]) -> None:
    non_negative_args = {
        "all_paths": ("max_paths",),
        "report_gates_by_type": ("limit",),
        "report_outputs_by_cone_size": ("min_gates",),
        "report_outputs_depth_greater_than": ("min_depth",),
        "check_fanout": ("max_fanout",),
        "insert_buffers_for_all_high_fanout": ("max_fanout",),
        "check_depth": ("max_depth",),
        "optimize_cone": ("max_depth",),
        "optimize_design_depth": ("max_depth",),
    }
    for key in non_negative_args.get(op, ()):
        if key in args and args[key] < 0:
            raise PlanValidationError(f"Operation '{op}' argument '{key}' must be non-negative.")

    if op in {"optimize_cone", "optimize_design_depth"} and "allowed_gates" in args:
        args["allowed_gates"] = _normalize_allowed_gate_list(op, args["allowed_gates"])
    if op == "optimize_design_depth" and "constraints" in args:
        args["constraints"] = _normalize_optimization_constraints(args["constraints"])
    if op in {"insert_buffers_for_fanout", "insert_buffers_for_all_high_fanout"} and args["max_fanout"] < 2:
        raise PlanValidationError(f"Operation '{op}' argument 'max_fanout' must be at least 2.")
    positive_optional_args = {
        "report_all_paths": ("max_paths",),
        "report_gate_type_connections": ("max_items",),
        "report_dffs_by_clock": ("max_items",),
    }
    for key in positive_optional_args.get(op, ()):
        if key in args and args[key] < 1:
            raise PlanValidationError(f"Operation '{op}' argument '{key}' must be at least 1.")

def _normalize_allowed_gate_list(op: str, allowed: Any) -> list[str]:
    valid_gate_types = {"and", "or", "not", "nand", "nor", "xor", "xnor", "buf"}
    if not isinstance(allowed, list) or not allowed:
        raise PlanValidationError(f"Operation '{op}' argument 'allowed_gates' must be a non-empty list of primitive gate type strings.")
    normalized: list[str] = []
    for item in allowed:
        if not isinstance(item, str) or not item.strip():
            raise PlanValidationError(f"Operation '{op}' argument 'allowed_gates' must be a non-empty list of primitive gate type strings.")
        gate_type = item.strip().lower()
        if gate_type not in valid_gate_types:
            raise PlanValidationError(f"Operation '{op}' argument 'allowed_gates' contains unsupported gate type '{item}'.")
        normalized.append(gate_type)
    return normalized


_WHOLE_DESIGN_CONSTRAINT_TARGETS = {
    "whole_design",
    "design",
    "netlist",
    "entire_design",
    "full_design",
    "whole netlist",
    "entire netlist",
    "full netlist",
}


def _normalize_optimization_constraints(constraints: Any) -> list[dict[str, Any]]:
    if not isinstance(constraints, list):
        raise PlanValidationError("Operation 'optimize_design_depth' argument 'constraints' must be a list.")
    normalized: list[dict[str, Any]] = []
    for index, constraint in enumerate(constraints, start=1):
        if not isinstance(constraint, dict):
            raise PlanValidationError(f"constraints[{index}] must be a JSON object.")
        unknown = set(constraint) - {"type", "target", "allowed_gates"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise PlanValidationError(f"constraints[{index}] has unknown field(s): {names}.")
        if constraint.get("type") != "cone_gate_library":
            raise PlanValidationError(f"constraints[{index}].type must be 'cone_gate_library'.")
        target = constraint.get("target")
        if not isinstance(target, str) or not target.strip():
            raise PlanValidationError(f"constraints[{index}].target must be a non-empty string.")
        target_name = target.strip()
        if target_name.lower() in _WHOLE_DESIGN_CONSTRAINT_TARGETS:
            raise PlanValidationError(
                f"constraints[{index}] is a whole-design gate-library constraint, "
                "but cone_gate_library requires a named target signal. Use an "
                "explicit whole-design rewrite such as replace_with_and_not before "
                "optimize_design_depth instead."
            )
        allowed = _normalize_allowed_gate_list("optimize_design_depth", constraint.get("allowed_gates"))
        normalized.append({"type": "cone_gate_library", "target": target_name, "allowed_gates": allowed})
    return normalized
def _reject_unknown_keys(obj: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(obj) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise PlanValidationError(f"Unknown field(s) in plan: {names}")


def _normalize_tool_step(step: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(step)
    args = normalized.get("args")
    op = normalized.get("op")
    if isinstance(args, dict):
        normalized_args = dict(args)
        if isinstance(op, str):
            _normalize_optional_null_args(op, normalized_args)
        if op == "find_gates" and normalized_args.get("name_contains") == "":
            normalized_args.pop("name_contains")
        if op == "find_path":
            if isinstance(normalized_args.get("avoid"), str):
                normalized_args["avoid"] = [normalized_args["avoid"]]
            if "avoiding" in normalized_args and "avoid" not in normalized_args:
                avoiding = normalized_args.pop("avoiding")
                normalized_args["avoid"] = [avoiding] if isinstance(avoiding, str) else avoiding
        if op == "report_gate_type_count_in_cone" and normalized_args.get("gate_type") == "":
            normalized["op"] = "logic_cone"
            normalized_args = {"target": normalized_args.get("target")}
        normalized["args"] = normalized_args
    if normalized.get("save_as") is None:
        normalized.pop("save_as", None)
    return normalized
