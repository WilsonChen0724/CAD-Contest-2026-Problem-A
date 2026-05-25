from __future__ import annotations

import json
from typing import Any


class PlanValidationError(ValueError):
    """Raised when an LLM plan is not safe to dispatch."""


SUPPORTED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_path",
    "all_paths_pass_through",
    "max_depth",
    "logic_cone",
    "report_outputs_by_cone_size",
    "find_gates",
    "same_clock_domain",
    "replace_buffers_with_and",
    "remove_dangling",
    "replace_inv_buf_with_inv",
    "replace_or_with_nand_not",
    "check_connectivity",
    "check_fanout",
    "check_depth",
    "unsupported",
}

_REQUIRED_ARGS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "begin_testcase": {"case_name": str},
    "read_design": {"path": str},
    "write_design": {"path": str},
    "find_path": {"src": str, "dst": str},
    "all_paths_pass_through": {"src": str, "dst": str, "node": str},
    "max_depth": {"src": str, "dst": str},
    "logic_cone": {"target": str},
    "report_outputs_by_cone_size": {"min_gates": int},
    "find_gates": {},
    "same_clock_domain": {"dff_a": str, "dff_b": str},
    "replace_buffers_with_and": {"extra_input": str},
    "remove_dangling": {},
    "replace_inv_buf_with_inv": {},
    "replace_or_with_nand_not": {"cone_target": str},
    "check_connectivity": {},
    "check_fanout": {"max_fanout": int},
    "check_depth": {"src": str, "dst": str, "max_depth": int},
    "unsupported": {"reason": str},
}

_OPTIONAL_ARGS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "find_path": {"avoid": list},
    "find_gates": {"gate_type": (str, type(None)), "name_contains": (str, type(None))},
    "replace_buffers_with_and": {"targets": list, "targets_from": str},
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
        - save_as, when present, must be a string.
    """
    if not isinstance(plan, dict):
        raise PlanValidationError("Plan must be a JSON object.")

    if "steps" in plan:
        _reject_unknown_keys(plan, {"steps"})
        steps = plan["steps"]
        if not isinstance(steps, list) or not steps:
            raise PlanValidationError("steps must be a non-empty list.")
        for index, step in enumerate(steps, start=1):
            try:
                _validate_single_step(step)
            except PlanValidationError as exc:
                raise PlanValidationError(f"Invalid step {index}: {exc}") from exc
        return plan

    _validate_single_step(plan)
    return plan


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

    if "save_as" in step and not isinstance(step["save_as"], str):
        raise PlanValidationError("Field 'save_as' must be a string.")

    _validate_args(op, args)


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
        if key in args and not isinstance(args[key], expected_type):
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


def _reject_unknown_keys(obj: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(obj) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise PlanValidationError(f"Unknown field(s) in plan: {names}")
