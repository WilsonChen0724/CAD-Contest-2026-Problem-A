from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.state import CurrentState
from parser.verilog_parser import parse_verilog
from parser.verilog_writer import write_verilog
from eda.analysis import find_gates, find_path, max_depth, logic_cone
from eda.transform import replace_buffers_with_and
from eda.verify import check_connectivity, check_fanout, check_depth

SUPPORTED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_gates",
    "find_path",
    "max_depth",
    "logic_cone",
    "replace_buffers_with_and",
    "check_connectivity",
    "check_fanout",
    "check_depth",
}

REQUIRED_ARGS = {
    "begin_testcase": ("case_name",),
    "read_design": ("path",),
    "write_design": ("path",),
    "find_gates": (),
    "find_path": ("src", "dst"),
    "max_depth": ("src", "dst"),
    "logic_cone": ("target",),
    "replace_buffers_with_and": ("extra_input",),
    "check_connectivity": (),
    "check_fanout": ("max_fanout",),
    "check_depth": ("src", "dst", "max_depth"),
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

    if op == "replace_buffers_with_and":
        _require_design(state)
        targets = args.get("targets")
        if targets is None and "targets_from" in args:
            targets = state.get_result(args["targets_from"], expected_kind="gate_list")
        if targets is None:
            raise ValueError('Tool call rejected: "targets" or "targets_from" is required.')
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise ValueError('Tool call rejected: "targets" must be a list of gate names.')
        result = replace_buffers_with_and(
            state.design,
            targets=targets,
            extra_input=args["extra_input"],
        )
        return f'Replaced {result["num_changed"]} buffer(s) with AND gate(s): {result["changed"]}'

    if op == "check_connectivity":
        _require_design(state)
        return str(check_connectivity(state.design))

    if op == "check_fanout":
        _require_design(state)
        return str(check_fanout(state.design, args["max_fanout"]))

    if op == "check_depth":
        _require_design(state)
        return str(check_depth(state.design, args["src"], args["dst"], args["max_depth"]))

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
