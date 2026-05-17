from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.state import CurrentState
from parser.verilog_parser import parse_verilog
from parser.verilog_writer import write_verilog
from eda.analysis import find_gates, find_path, max_depth, logic_cone
from eda.transform import replace_buffers_with_and
from eda.verify import check_connectivity, check_fanout, check_depth


def dispatch_plan(state: CurrentState, plan: dict[str, Any]) -> str:
    """
    Execute a single tool call or a multi-step plan.
    """
    if "steps" in plan:
        messages = []
        for step in plan["steps"]:
            messages.append(dispatch_plan(state, step))
        return "\n".join(messages)

    op = plan.get("op")
    args = plan.get("args", {}) or {}
    save_as = plan.get("save_as")

    if op == "begin_testcase":
        case_name = args["case_name"]
        state.begin_testcase(case_name)
        return (
            f'Acknowledged. Initialized testcase "{case_name}". '
            f"All subsequent responses will be recorded to {state.log_path}."
        )

    if op == "read_design":
        path = args["path"]
        state.design = parse_verilog(path)
        return f'Loaded gate-level Verilog from "{path}" successfully.\n- {state.design.summary()}'

    if op == "write_design":
        _require_design(state)
        path = args["path"]
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
            state.previous_results[save_as] = result
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
            state.previous_results[save_as] = gates
        return f'Logic cone of "{args["target"]}" contains {len(gates)} gates:\n' + "\n".join(gates)

    if op == "replace_buffers_with_and":
        _require_design(state)
        targets = args.get("targets")
        if targets is None and "targets_from" in args:
            targets = state.previous_results.get(args["targets_from"], [])
        result = replace_buffers_with_and(
            state.design,
            targets=targets or [],
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

    return f"Unsupported operation: {op}"


def _require_design(state: CurrentState) -> None:
    if state.design is None:
        raise RuntimeError("No design has been loaded yet.")


def _format_list_result(title: str, items: list[str]) -> str:
    if not items:
        return f"{title}: none."
    lines = [f"{title}: {len(items)}"]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item}")
    return "\n".join(lines)
