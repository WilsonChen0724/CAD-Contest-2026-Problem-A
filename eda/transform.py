from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from math import ceil
from functools import wraps
from pathlib import Path
import re
import tempfile
from typing import Any, TypeVar

from eda.analysis import find_path, logic_cone, max_depth, primary_output_cone_sizes
from eda.design import DFF, Design, Gate, is_constant
from eda.graph import rebuild_graph
from eda.verify import check_connectivity
from parser.verilog_parser import _dff_wrapper_name, parse_verilog
from parser.verilog_writer import write_verilog
from parser.yosys_tools import quote_yosys_path, run_yosys_script


CONE_YOSYS_ABC_MIN_GATES = 8
CONE_YOSYS_ABC_MAX_GATES = 3000
ABC_DEPTH_TARGET_RATIO = 0.75

# todo
# 1. make_unique_wire_name()
# 2. make_unique_gate_name()
# 3. get_gate()
# 4. replace_gate_safely()
# day 2. remove_dangling()
# day 2. replace_inv_buf_with_inv
# day 3. replace_or_with_nand_not
# day 4: after every transformation auto verification
# day 5: insert_buffers_for_fanout
# day 6: balance_depth_with_buffers
# day 7: optimize_cone
# day 8: safe net rename
# day 9: constant propagation
# day 10: NAND const-1 to inverter rewrite

_F = TypeVar("_F", bound=Callable[..., Any])


def _rebuild_graph_after_transform(func: _F) -> _F:
    """Ensure every transform leaves Design drivers/fanouts in sync."""
    @wraps(func)
    def wrapper(design: Design, *args: Any, **kwargs: Any) -> Any:
        result = func(design, *args, **kwargs)
        rebuild_graph(design)
        return result

    return wrapper  # type: ignore[return-value]


@_rebuild_graph_after_transform
def replace_buffers_with_and(design: Design, targets: list[str], extra_input: str) -> dict:
    """Replace selected one-input buffers with two-input AND gates."""
    changed = []
    for name in targets:
        gate = design.gates.get(name)
        if gate is None:
            continue
        if gate.type != "buf":
            continue
        if len(gate.inputs) != 1:
            continue
        gate.type = "and"
        gate.inputs = [gate.inputs[0], extra_input]
        changed.append(name)
        design.wires.add(extra_input)

    return {"changed": changed, "num_changed": len(changed)}


@_rebuild_graph_after_transform
def remove_dangling(design: Design) -> dict:
    """Remove gates and nets that do not affect any primary output."""
    rebuild_graph(design)
    live_gates: set[str] = set()
    live_dffs: set[str] = set()
    live_nets: set[str] = set(design.outputs)
    stack = list(design.outputs)

    while stack:
        net = stack.pop()
        driver = design.drivers.get(net)
        if not driver:
            continue

        kind, name = driver.split(":", 1)
        if kind == "GATE":
            if name in live_gates:
                continue
            gate = design.gates[name]
            live_gates.add(name)
            live_nets.add(gate.output)
            for input_net in gate.inputs:
                live_nets.add(input_net)
                if not is_constant(input_net):
                    stack.append(input_net)
        elif kind == "DFF":
            if name in live_dffs:
                continue
            dff = design.dffs[name]
            live_dffs.add(name)
            live_nets.add(dff.q)
            for input_net in (dff.d, dff.clk, dff.rst):
                if input_net and not is_constant(input_net):
                    live_nets.add(input_net)
                    stack.append(input_net)

    removed_gates = sorted(set(design.gates) - live_gates)
    removed_dffs = sorted(set(design.dffs) - live_dffs)
    for name in removed_gates:
        del design.gates[name]
    for name in removed_dffs:
        del design.dffs[name]

    used_nets = set(design.inputs) | set(design.outputs)
    for gate in design.gates.values():
        used_nets.add(gate.output)
        used_nets.update(net for net in gate.inputs if not is_constant(net))
    for dff in design.dffs.values():
        used_nets.add(dff.q)
        for net in (dff.d, dff.clk, dff.rst):
            if net and not is_constant(net):
                used_nets.add(net)

    removed_nets = sorted(net for net in design.wires if net not in used_nets and not is_constant(net))
    design.wires = {net for net in design.wires if net in used_nets or is_constant(net)}
    return {
        "removed_gates": removed_gates,
        "removed_dffs": removed_dffs,
        "removed_nets": removed_nets,
        "num_removed_gates": len(removed_gates),
        "num_removed_dffs": len(removed_dffs),
        "num_removed_nets": len(removed_nets),
    }


@_rebuild_graph_after_transform
def replace_inv_buf_with_inv(design: Design) -> dict:
    """Collapse safe inverter-buffer chains into one inverter."""
    rebuild_graph(design)
    changed: list[dict[str, str]] = []

    for buf_name, buf_gate in list(design.gates.items()):
        if buf_gate.type != "buf" or len(buf_gate.inputs) != 1:
            continue

        mid_net = buf_gate.inputs[0]
        driver = design.drivers.get(mid_net)
        if not driver or not driver.startswith("GATE:"):
            continue

        inv_name = driver.split(":", 1)[1]
        inv_gate = design.gates.get(inv_name)
        if inv_gate is None or inv_gate.type != "not" or len(inv_gate.inputs) != 1:
            continue

        if design.fanouts.get(mid_net, []) != [f"GATE:{buf_name}"]:
            continue

        buf_gate.type = "not"
        buf_gate.inputs = [inv_gate.inputs[0]]
        del design.gates[inv_name]
        if mid_net not in design.inputs and mid_net not in design.outputs:
            design.wires.discard(mid_net)
        changed.append(
            {
                "removed_inverter": inv_name,
                "rewritten_buffer": buf_name,
                "removed_net": mid_net,
            }
        )
        rebuild_graph(design)

    return {"changed": changed, "num_changed": len(changed)}


@_rebuild_graph_after_transform
def replace_or_with_nand_not(design: Design, cone_target: str) -> dict:
    """Rewrite OR gates in a cone using equivalent NAND/NOT logic."""
    cone_gates = set(logic_cone(design, cone_target))
    changed: list[dict[str, Any]] = []
    names = _CachedNameAllocator(design)

    for gate_name in sorted(cone_gates):
        gate = design.gates.get(gate_name)
        if gate is None or gate.type != "or" or len(gate.inputs) != 2:
            continue

        input_a, input_b = gate.inputs
        not_a_net = names.wire(f"{gate.name}_na")
        not_b_net = names.wire(f"{gate.name}_nb")
        not_a_name = names.gate_name(f"{gate.name}_not_a")
        not_b_name = names.gate_name(f"{gate.name}_not_b")

        design.add_gate(Gate(name=not_a_name, type="not", inputs=[input_a], output=not_a_net))
        design.add_gate(Gate(name=not_b_name, type="not", inputs=[input_b], output=not_b_net))
        gate.type = "nand"
        gate.inputs = [not_a_net, not_b_net]

        changed.append(
            {
                "rewritten_gate": gate_name,
                "added_gates": [not_a_name, not_b_name],
                "added_nets": [not_a_net, not_b_net],
            }
        )

    return {"changed": changed, "cone_target": cone_target, "num_changed": len(changed)}


@_rebuild_graph_after_transform
def replace_net_references(design: Design, old_net: str, new_net: str) -> dict:
    """
    Replace every structural reference to one net name.

    This helper updates declarations plus primitive/DFF pins. It does not check
    for name collisions, so user-facing transforms should usually call
    rename_net() instead.
    """
    if is_constant(old_net) or is_constant(new_net):
        raise ValueError("Constant values cannot be renamed.")
    if old_net == new_net:
        return {"old_net": old_net, "new_net": new_net, "num_references": 0, "locations": []}
    if old_net not in design.all_nets():
        raise ValueError(f'Net not found: "{old_net}"')

    locations: list[str] = []
    old_was_input = old_net in design.inputs
    old_was_output = old_net in design.outputs
    old_was_wire = old_net in design.wires

    if old_was_input:
        design.inputs.remove(old_net)
        design.inputs.add(new_net)
        locations.append("input")
    if old_was_output:
        design.outputs.remove(old_net)
        design.outputs.add(new_net)
        locations.append("output")
    if old_was_wire:
        design.wires.remove(old_net)
        design.wires.add(new_net)
        locations.append("wire")

    for gate in design.gates.values():
        for index, input_net in enumerate(gate.inputs):
            if input_net == old_net:
                gate.inputs[index] = new_net
                locations.append(f"{gate.name}.input[{index}]")
        if gate.output == old_net:
            gate.output = new_net
            locations.append(f"{gate.name}.output")

    for dff in design.dffs.values():
        if dff.d == old_net:
            dff.d = new_net
            locations.append(f"{dff.name}.D")
        if dff.q == old_net:
            dff.q = new_net
            locations.append(f"{dff.name}.Q")
        if dff.clk == old_net:
            dff.clk = new_net
            locations.append(f"{dff.name}.CLK")
        if dff.rst == old_net:
            dff.rst = new_net
            locations.append(f"{dff.name}.RST")

    if _referenced_as_internal_net(design, new_net):
        design.wires.add(new_net)
    if not old_was_input and not old_was_output:
        design.wires.discard(old_net)

    return {
        "old_net": old_net,
        "new_net": new_net,
        "num_references": len(locations),
        "locations": locations,
    }


@_rebuild_graph_after_transform
def rename_net(design: Design, old_net: str, new_net: str) -> dict:
    """Rename one net without allowing collisions with existing nets."""
    if is_constant(old_net) or is_constant(new_net):
        raise ValueError("Constant values cannot be renamed.")
    if old_net == new_net:
        return {"old_net": old_net, "new_net": new_net, "num_references": 0, "locations": []}
    if old_net not in design.all_nets():
        raise ValueError(f'Net not found: "{old_net}"')
    if new_net in design.all_nets():
        raise ValueError(f'Cannot rename "{old_net}" to existing net "{new_net}".')
    if new_net in design.gates or new_net in design.dffs:
        raise ValueError(f'Cannot rename "{old_net}" to existing instance name "{new_net}".')
    return replace_net_references(design, old_net, new_net)



@_rebuild_graph_after_transform
def rename_gate(design: Design, old_name: str, new_name: str) -> dict:
    """Rename one gate or DFF instance without changing net connectivity."""
    if old_name == new_name:
        if old_name in design.gates:
            return {"old_name": old_name, "new_name": new_name, "kind": "gate"}
        if old_name in design.dffs:
            return {"old_name": old_name, "new_name": new_name, "kind": "dff"}
        raise ValueError(f'Instance not found: "{old_name}"')
    if new_name in design.gates or new_name in design.dffs:
        raise ValueError(f'Cannot rename "{old_name}" to existing instance name "{new_name}".')
    if new_name in design.all_nets():
        raise ValueError(f'Cannot rename "{old_name}" to existing net "{new_name}".')

    if old_name in design.gates:
        gate = design.gates.pop(old_name)
        gate.name = new_name
        design.gates[new_name] = gate
        return {"old_name": old_name, "new_name": new_name, "kind": "gate"}
    if old_name in design.dffs:
        dff = design.dffs.pop(old_name)
        dff.name = new_name
        design.dffs[new_name] = dff
        return {"old_name": old_name, "new_name": new_name, "kind": "dff"}
    raise ValueError(f'Instance not found: "{old_name}"')


@_rebuild_graph_after_transform
def reconnect_gate_input(design: Design, gate_name: str, pin: str, new_net: str) -> dict:
    """Reconnect one positional primitive-gate input pin."""
    gate = design.gates.get(gate_name)
    if gate is None:
        raise ValueError(f'Gate not found: "{gate_name}"')
    index = _pin_to_input_index(pin)
    if index >= len(gate.inputs):
        raise ValueError(f'Gate "{gate_name}" has no input pin {pin}.')
    old_net = gate.inputs[index]
    gate.inputs[index] = new_net
    if not is_constant(new_net):
        design.wires.add(new_net)
    return {
        "gate": gate_name,
        "pin": pin,
        "pin_index": index,
        "old_net": old_net,
        "new_net": new_net,
    }


@_rebuild_graph_after_transform
def constant_propagation(design: Design, max_changes: int | None = None) -> dict:
    """Simplify primitive gates with constant or redundant inputs."""
    changed: list[dict[str, Any]] = []
    max_iterations = max(1, len(design.gates) + 1)

    for _ in range(max_iterations):
        rebuild_graph(design)
        rewrite = None
        for gate_name in sorted(design.gates):
            rewrite = _simplify_constant_gate(design, gate_name)
            if rewrite is not None:
                changed.append(rewrite)
                if max_changes is not None and len(changed) >= max_changes:
                    return {"changed": changed, "num_changed": len(changed), "bounded": True, "max_changes": max_changes}
                break
        if rewrite is None:
            break
    else:
        raise RuntimeError("constant_propagation did not converge.")

    return {"changed": changed, "num_changed": len(changed), "bounded": False}


@_rebuild_graph_after_transform
def replace_nand_const1_with_not(design: Design) -> dict:
    """Rewrite 2-input NAND gates with one constant-1 input into inverters."""
    changed: list[dict[str, str]] = []
    for gate in design.gates.values():
        if gate.type != "nand" or len(gate.inputs) != 2:
            continue
        a, b = gate.inputs
        if _is_const_true(a) and not is_constant(b):
            gate.type = "not"
            gate.inputs = [b]
        elif _is_const_true(b) and not is_constant(a):
            gate.type = "not"
            gate.inputs = [a]
        else:
            continue
        changed.append({"gate": gate.name, "replacement": "not", "output": gate.output})

    return {"changed": changed, "num_changed": len(changed)}


@_rebuild_graph_after_transform
def insert_buffers_for_fanout(design: Design, net: str, max_fanout: int) -> dict:
    """
    Insert a balanced buffer tree so the selected net's fanout stays bounded.

    Primary-output sinks cannot be renamed safely, so they remain directly tied
    to the original net and consume fanout slots at the root.
    """
    if max_fanout < 2:
        raise ValueError("insert_buffers_for_fanout requires max_fanout >= 2.")

    rebuild_graph(design)
    if net not in design.all_nets():
        raise ValueError(f'Net not found: "{net}"')

    original_sinks = list(design.fanouts.get(net, []))
    po_sinks = [sink for sink in original_sinks if sink.startswith("PO:")]
    movable_sinks = [sink for sink in original_sinks if not sink.startswith("PO:")]
    if len(original_sinks) <= max_fanout:
        return {
            "net": net,
            "max_fanout": max_fanout,
            "original_fanout": len(original_sinks),
            "inserted_buffers": [],
            "inserted_nets": [],
            "num_inserted_buffers": 0,
            "final_net_fanout": len(design.fanouts.get(net, [])),
            "final_max_fanout": _max_fanout(design),
        }

    if len(po_sinks) >= max_fanout and movable_sinks:
        raise ValueError(
            f'Cannot bound fanout for "{net}": primary-output sinks already '
            f"consume {len(po_sinks)} of {max_fanout} root fanout slots."
        )

    inserted_buffers: list[str] = []
    inserted_nets: list[str] = []

    def add_buffer(source_net: str) -> str:
        buffer_net = design.make_unique_wire_name(f"{net}_fanout_buf")
        buffer_name = design.make_unique_gate_name(f"{net}_fanout_buf_gate")
        design.add_gate(Gate(name=buffer_name, type="buf", inputs=[source_net], output=buffer_net))
        inserted_buffers.append(buffer_name)
        inserted_nets.append(buffer_net)
        return buffer_net

    def route_sinks(source_net: str, sinks: Sequence[str], available_fanout: int) -> None:
        if not sinks:
            return
        if len(sinks) <= available_fanout:
            for sink in sinks:
                _redirect_sink(design, sink, old_net=net, new_net=source_net)
            return

        branch_count = min(available_fanout, len(sinks))
        group_size = ceil(len(sinks) / branch_count)
        for start in range(0, len(sinks), group_size):
            branch_sinks = sinks[start : start + group_size]
            branch_net = add_buffer(source_net)
            route_sinks(branch_net, branch_sinks, max_fanout)

    root_available_fanout = max_fanout - len(po_sinks)
    route_sinks(net, movable_sinks, root_available_fanout)
    rebuild_graph(design)

    return {
        "net": net,
        "max_fanout": max_fanout,
        "original_fanout": len(original_sinks),
        "inserted_buffers": inserted_buffers,
        "inserted_nets": inserted_nets,
        "num_inserted_buffers": len(inserted_buffers),
        "final_net_fanout": len(design.fanouts.get(net, [])),
        "final_max_fanout": _max_fanout(design),
    }


@_rebuild_graph_after_transform
def insert_dedicated_buffers_for_each_load(design: Design, net: str) -> dict:
    """
    Insert one BUF per current direct load of a net.

    Gate and DFF input sinks are redirected through dedicated new buffer output
    nets. Primary-output sinks are left direct because preserving a primary
    output name while inserting a buffer requires replacing the existing driver,
    which is a different transform.
    """
    rebuild_graph(design)
    if net not in design.all_nets():
        raise ValueError(f'Net not found: "{net}"')

    original_sinks = list(design.fanouts.get(net, []))
    inserted_buffers: list[str] = []
    inserted_nets: list[str] = []
    skipped_sinks: list[str] = []

    for sink in original_sinks:
        if sink.startswith("PO:"):
            skipped_sinks.append(sink)
            continue
        buffer_net = design.make_unique_wire_name(f"{net}_dedicated_buf")
        buffer_name = design.make_unique_gate_name(f"{net}_dedicated_buf_gate")
        design.add_gate(Gate(name=buffer_name, type="buf", inputs=[net], output=buffer_net))
        _redirect_sink(design, sink, old_net=net, new_net=buffer_net)
        inserted_buffers.append(buffer_name)
        inserted_nets.append(buffer_net)

    rebuild_graph(design)
    return {
        "net": net,
        "original_loads": len(original_sinks),
        "inserted_buffers": inserted_buffers,
        "inserted_nets": inserted_nets,
        "skipped_sinks": skipped_sinks,
        "num_inserted_buffers": len(inserted_buffers),
        "final_direct_loads": len(design.fanouts.get(net, [])),
    }


@_rebuild_graph_after_transform
def collapse_back_to_back_inverters(design: Design) -> dict:
    """Collapse safe NOT chains in graph-wide batches."""
    changed: list[dict[str, Any]] = []
    num_changed = 0
    max_rounds = max(1, len(design.gates) + 1)

    for _ in range(max_rounds):
        rebuild_graph(design)
        round_changes, round_count = _collapse_inverter_chains_once(design)
        if round_count == 0:
            break
        changed.extend(round_changes)
        num_changed += round_count
    else:
        raise RuntimeError("collapse_back_to_back_inverters did not converge.")

    return {"changed": changed, "num_changed": num_changed}


@_rebuild_graph_after_transform
def balance_depth_with_buffers(
    design: Design,
    src: str,
    dsts: list[str],
    minimize_buffers: bool = True,
) -> dict:
    """
    Insert endpoint buffer chains so all selected src-to-dst depths match.

    This first version supports independent, gate-driven destination nets. That
    keeps the transform local and makes the inserted buffer count minimal for
    the current endpoint-padding strategy.
    """
    del minimize_buffers  # Endpoint padding is already minimal for this strategy.
    if not dsts:
        raise ValueError("balance_depth_with_buffers requires at least one destination.")
    if src not in design.all_nets():
        raise ValueError(f'Source net not found: "{src}"')

    rebuild_graph(design)
    _require_independent_destinations(design, dsts)
    initial_depths = _depths_from_source(design, src, dsts)
    target_depth = max(initial_depths.values())

    changed: list[dict[str, Any]] = []
    for dst in dsts:
        deficit = target_depth - initial_depths[dst]
        if deficit <= 0:
            continue
        changed.append(_insert_buffer_chain_before_net(design, dst, deficit))
        rebuild_graph(design)

    final_depths = _depths_from_source(design, src, dsts)
    return {
        "src": src,
        "dsts": dsts,
        "initial_depths": initial_depths,
        "target_depth": target_depth,
        "final_depths": final_depths,
        "changed": changed,
        "num_inserted_buffers": sum(len(item["added_gates"]) for item in changed),
    }


@_rebuild_graph_after_transform
def optimize_cone(
    design: Design,
    target: str,
    max_depth: int | None = None,
    minimize_gate_count: bool = True,
    allow_yosys_abc: bool = True,
) -> dict:
    """
    Locally simplify a target fanin cone with function-preserving rewrites.

    DFF.Q targets are treated as sequential boundaries for analysis, but for
    transformation requests we resolve them to the corresponding D input cone.
    This keeps LLM tool calls simple while matching optimization intent.
    """
    del minimize_gate_count  # The current rule set always reduces gate count when possible.
    if max_depth is not None and max_depth < 0:
        raise ValueError("optimize_cone requires max_depth >= 0 when provided.")

    rebuild_graph(design)
    resolved = _resolve_optimization_cone_target(design, target)
    resolved_target = resolved["target"]
    if resolved_target not in design.all_nets():
        raise ValueError(f'Target net not found: "{target}"')

    initial_gates = logic_cone(design, resolved_target)
    initial_depth = _cone_max_depth(design, resolved_target)
    changed: list[dict[str, Any]] = []
    max_iterations = max(1, len(design.gates) + 1)

    for _ in range(max_iterations):
        cone_gates = set(logic_cone(design, resolved_target))
        rewrite = _simplify_double_inverter_in_cone(design, cone_gates)
        if rewrite is None:
            rewrite = _remove_internal_buffer_in_cone(design, cone_gates)
        if rewrite is None:
            break
        changed.append(rewrite)
        rebuild_graph(design)
    else:
        raise RuntimeError("optimize_cone did not converge.")

    final_depth = _cone_max_depth(design, resolved_target)
    final_gates = logic_cone(design, resolved_target)
    if allow_yosys_abc:
        abc_result = _try_yosys_abc_for_small_cone(
            design,
            target=target,
            resolved_target=resolved_target,
            max_depth=max_depth,
            initial_gate_count=len(initial_gates),
            initial_depth=initial_depth,
            current_gate_count=len(final_gates),
            current_depth=final_depth,
        )
        if abc_result is not None:
            abc_result["changed"] = changed + abc_result.get("changed", [])
            abc_result["num_changed"] = len(abc_result["changed"])
            abc_result["target_resolution"] = resolved
            return abc_result

    return {
        "target": target,
        "resolved_target": resolved_target,
        "target_resolution": resolved,
        "engine": "local_cleanup",
        "max_depth": max_depth,
        "initial_gate_count": len(initial_gates),
        "final_gate_count": len(final_gates),
        "removed_gate_count": len(initial_gates) - len(final_gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "changed": changed,
        "num_changed": len(changed),
    }


def _try_yosys_abc_for_small_cone(
    design: Design,
    target: str,
    resolved_target: str,
    max_depth: int | None,
    initial_gate_count: int,
    initial_depth: int,
    current_gate_count: int,
    current_depth: int,
) -> dict[str, Any] | None:
    if current_gate_count < CONE_YOSYS_ABC_MIN_GATES or current_gate_count > CONE_YOSYS_ABC_MAX_GATES:
        return None

    try:
        candidate, abc_summary = _optimize_cone_with_yosys_abc(
            design,
            resolved_target=resolved_target,
            max_depth=max_depth,
            current_gate_count=current_gate_count,
            current_depth=current_depth,
        )
    except Exception:
        return None

    if resolved_target not in candidate.all_nets():
        return None
    candidate_depth = _cone_max_depth(candidate, resolved_target)
    candidate_gates = logic_cone(candidate, resolved_target)
    improved_depth = candidate_depth < current_depth
    improved_gate_count = candidate_depth == current_depth and len(candidate_gates) < current_gate_count
    if not (improved_depth or improved_gate_count):
        return None

    _replace_design_contents(design, candidate)
    return {
        "target": target,
        "resolved_target": resolved_target,
        "engine": "local_plus_cone_local_yosys_abc",
        "max_depth": max_depth,
        "initial_gate_count": initial_gate_count,
        "final_gate_count": len(candidate_gates),
        "removed_gate_count": initial_gate_count - len(candidate_gates),
        "initial_depth": initial_depth,
        "final_depth": candidate_depth,
        "target_met": max_depth is None or candidate_depth <= max_depth,
        "changed": [{"rule": "cone_local_yosys_abc", "summary": abc_summary}],
        "num_changed": 1,
    }


def _abc_candidate_depth_targets(current_depth: int) -> list[tuple[str, int | None]]:
    soft_target = max(1, ceil(current_depth * ABC_DEPTH_TARGET_RATIO))
    return [
        (f"target_{soft_target}", soft_target),
        ("plain_abc", None),
    ]


def _optimize_cone_with_yosys_abc(
    design: Design,
    *,
    resolved_target: str,
    max_depth: int | None,
    current_gate_count: int,
    current_depth: int,
) -> tuple[Design, dict[str, Any]]:
    cone_gate_names = set(logic_cone(design, resolved_target))
    if not cone_gate_names:
        raise ValueError("Target cone is empty.")
    if _cone_has_external_internal_fanout(design, cone_gate_names, resolved_target):
        raise ValueError("Cone has shared internal fanout; multi-output cone extraction is not enabled.")

    cone_design, boundary_inputs = _build_cone_design(design, resolved_target, cone_gate_names)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        raw_input = tmp_dir / "cone_input.v"
        yosys_input = tmp_dir / "cone_yosys_input.v"
        optimized_output = tmp_dir / "cone_optimized.v"
        write_verilog(cone_design, raw_input)
        _write_yosys_abc_input(raw_input, yosys_input)
        attempts: list[str] = []
        candidates: list[tuple[int, int, Design, dict[str, Any]]] = []
        for label, target_depth in _abc_candidate_depth_targets(current_depth):
            script = _build_yosys_abc_cone_script(
                input_path=yosys_input,
                output_path=optimized_output,
                top_module=cone_design.module_name,
                max_depth=target_depth,
            )
            optimized_cone, reason = _run_yosys_depth_attempt(
                script,
                tmp_dir=tmp_dir,
                output_path=optimized_output,
                timeout=_cone_yosys_abc_timeout(current_gate_count),
                label=f"cone_local_yosys_abc_{label}",
            )
            if optimized_cone is None:
                attempts.append(reason)
                continue

            candidate = deepcopy(design)
            _splice_optimized_cone(
                candidate,
                resolved_target=resolved_target,
                old_cone_gate_names=cone_gate_names,
                optimized_cone=optimized_cone,
                boundary_inputs=boundary_inputs,
            )
            rebuild_graph(candidate)
            connectivity = check_connectivity(candidate)
            if not connectivity.get("ok"):
                attempts.append(
                    "cone-local Yosys/ABC failed connectivity check: "
                    f"{_summarize_connectivity_failure(connectivity)}"
                )
                continue

            candidate_depth = _cone_max_depth(candidate, resolved_target)
            candidate_gate_count = len(logic_cone(candidate, resolved_target))
            candidates.append(
                (
                    candidate_depth,
                    candidate_gate_count,
                    candidate,
                    {
                        "engine": "cone_local_yosys_abc",
                        "attempt": label,
                        "abc_target_depth": target_depth,
                        "boundary_inputs": boundary_inputs,
                        "optimized_cone_gates": len(optimized_cone.gates),
                    },
                )
            )

    if not candidates:
        raise RuntimeError("; ".join(attempts) or "cone-local Yosys/ABC did not produce a usable candidate.")
    _, _, best_candidate, best_summary = min(candidates, key=lambda item: (item[0], item[1]))
    return best_candidate, best_summary


def _cone_has_external_internal_fanout(
    design: Design,
    cone_gate_names: set[str],
    resolved_target: str,
) -> bool:
    rebuild_graph(design)
    for gate_name in cone_gate_names:
        gate = design.gates[gate_name]
        if gate.output == resolved_target:
            continue
        for sink in design.fanouts.get(gate.output, []):
            if sink.startswith("GATE:") and sink.split(":", 1)[1] in cone_gate_names:
                continue
            return True
    return False


def _build_cone_design(
    design: Design,
    resolved_target: str,
    cone_gate_names: set[str],
) -> tuple[Design, list[str]]:
    boundary_inputs: list[str] = []
    boundary_seen: set[str] = set()
    for gate_name in sorted(cone_gate_names):
        gate = design.gates[gate_name]
        for net in gate.inputs:
            if is_constant(net):
                continue
            driver = design.drivers.get(net)
            if driver and driver.startswith("GATE:") and driver.split(":", 1)[1] in cone_gate_names:
                continue
            if net not in boundary_seen:
                boundary_seen.add(net)
                boundary_inputs.append(net)

    cone_design = Design(module_name="cone_opt", inputs=set(boundary_inputs), outputs={resolved_target})
    for gate_name in sorted(cone_gate_names):
        gate = design.gates[gate_name]
        cone_design.add_gate(
            Gate(
                name=gate.name,
                type=gate.type,
                inputs=list(gate.inputs),
                output=gate.output,
                attrs=dict(gate.attrs),
            )
        )
    rebuild_graph(cone_design)
    return cone_design, boundary_inputs


def _splice_optimized_cone(
    design: Design,
    *,
    resolved_target: str,
    old_cone_gate_names: set[str],
    optimized_cone: Design,
    boundary_inputs: list[str],
) -> None:
    boundary = set(boundary_inputs)
    old_internal_outputs = {
        design.gates[name].output
        for name in old_cone_gate_names
        if name in design.gates and design.gates[name].output != resolved_target
    }
    for gate_name in old_cone_gate_names:
        design.gates.pop(gate_name, None)

    net_map: dict[str, str] = {net: net for net in boundary}
    net_map[resolved_target] = resolved_target
    for constant in ("1'b0", "1'b1", "1'bx", "1'bz", "0", "1"):
        net_map[constant] = constant

    for gate_name in sorted(optimized_cone.gates):
        gate = optimized_cone.gates[gate_name]
        mapped_inputs = [_map_cone_net(design, net_map, net, resolved_target) for net in gate.inputs]
        mapped_output = _map_cone_net(design, net_map, gate.output, resolved_target)
        new_gate_name = design.make_unique_gate_name(f"{resolved_target}_cone_abc_{gate.name}")
        design.add_gate(
            Gate(
                name=new_gate_name,
                type=gate.type,
                inputs=mapped_inputs,
                output=mapped_output,
                attrs=dict(gate.attrs),
            )
        )

    for net in old_internal_outputs:
        if net not in boundary and net not in design.outputs:
            design.wires.discard(net)
    rebuild_graph(design)


def _map_cone_net(design: Design, net_map: dict[str, str], net: str, resolved_target: str) -> str:
    if net in net_map:
        return net_map[net]
    if is_constant(net):
        net_map[net] = net
        return net
    mapped = design.make_unique_wire_name(f"{resolved_target}_cone_{net}")
    net_map[net] = mapped
    return mapped


def _cone_yosys_abc_timeout(gate_count: int) -> float:
    if gate_count <= 100:
        return 15.0
    if gate_count <= 500:
        return 25.0
    return 40.0

def _resolve_optimization_cone_target(design: Design, target: str) -> dict[str, Any]:
    if target not in design.all_nets():
        raise ValueError(f'Target net not found: "{target}"')

    direct_dff = _dff_driving_q(design, target)
    if direct_dff is not None and not is_constant(direct_dff.d):
        return {
            "target": direct_dff.d,
            "original_target": target,
            "kind": "dff_q_to_d",
            "dff": direct_dff.name,
            "reason": "resolved DFF Q target to its D input cone",
        }

    driver = design.drivers.get(target)
    if driver and driver.startswith("GATE:"):
        gate_name = driver.split(":", 1)[1]
        gate = design.gates.get(gate_name)
        if gate and gate.type == "buf" and len(gate.inputs) == 1:
            source = gate.inputs[0]
            source_dff = _dff_driving_q(design, source)
            if source_dff is not None and not is_constant(source_dff.d):
                return {
                    "target": source_dff.d,
                    "original_target": target,
                    "kind": "po_buf_dff_q_to_d",
                    "dff": source_dff.name,
                    "reason": "resolved primary-output buffer driven by DFF Q to the D input cone",
                }

    return {
        "target": target,
        "original_target": target,
        "kind": "direct",
        "reason": "using the requested combinational fanin cone",
    }


def _dff_driving_q(design: Design, net: str) -> DFF | None:
    for dff in design.dffs.values():
        if dff.q == net:
            return dff
    return None


@_rebuild_graph_after_transform
def insert_buffers_for_all_high_fanout(
    design: Design,
    max_fanout: int,
    max_changed_nets: int | None = None,
) -> dict:
    """Apply fanout buffering to every net currently exceeding max_fanout."""
    if max_fanout < 2:
        raise ValueError("insert_buffers_for_all_high_fanout requires max_fanout >= 2.")

    rebuild_graph(design)
    candidates = sorted(
        (
            net for net, sinks in design.fanouts.items()
            if not is_constant(net) and len(sinks) > max_fanout
        ),
        key=lambda net: (-len(design.fanouts.get(net, [])), net),
    )
    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for index, net in enumerate(candidates):
        if max_changed_nets is not None and len(changed) >= max_changed_nets:
            for skipped_net in candidates[index:]:
                skipped.append(
                    {
                        "net": skipped_net,
                        "reason": f"bounded mode reached {max_changed_nets} changed net(s)",
                    }
                )
            break
        rebuild_graph(design)
        if len(design.fanouts.get(net, [])) <= max_fanout:
            continue
        try:
            result = insert_buffers_for_fanout(design, net, max_fanout)
        except ValueError as exc:
            skipped.append({"net": net, "reason": str(exc)})
            continue
        if result.get("num_inserted_buffers", 0):
            changed.append(result)

    return {
        "max_fanout": max_fanout,
        "max_changed_nets": max_changed_nets,
        "attempted_nets": candidates,
        "changed": changed,
        "skipped": skipped,
        "num_changed_nets": len(changed),
        "num_inserted_buffers": sum(item.get("num_inserted_buffers", 0) for item in changed),
        "final_max_fanout": _max_fanout(design),
    }


@_rebuild_graph_after_transform
def optimize_design_depth(
    design: Design,
    max_depth: int | None = None,
    max_outputs: int | None = None,
) -> dict:
    """Run Yosys/ABC-backed full-design depth optimization, with a local fallback."""
    if max_depth is not None and max_depth < 0:
        raise ValueError("optimize_design_depth requires max_depth >= 0 when provided.")
    if max_outputs is not None and max_outputs < 1:
        raise ValueError("optimize_design_depth requires max_outputs >= 1 when provided.")

    gate_count = len(design.gates)
    has_fanout_buffers = any("__fanout_buf_" in net for net in design.all_nets())
    adaptive_k = _adaptive_depth_topk(gate_count, has_fanout_buffers)
    effective_max_outputs = max_outputs if max_outputs is not None else adaptive_k

    if max_outputs is None:
        if gate_count > 10000:
            return _optimize_design_depth_locally(
                design,
                max_depth=max_depth,
                max_outputs=effective_max_outputs,
                engine="adaptive_topk_critical_cones",
                bounded_reason=f"large design: skipped full-design Yosys/ABC and selected top {effective_max_outputs} critical cone target(s) for bounded cleanup",
                allow_cone_yosys=True,
            )
        try:
            return _optimize_design_depth_with_yosys_abc(design, max_depth=max_depth)
        except Exception as exc:
            result = _optimize_design_depth_locally(
                design,
                max_depth=max_depth,
                max_outputs=effective_max_outputs,
                engine="topk_after_yosys_fallback",
                bounded_reason=f"Yosys/ABC failed; attempted top {effective_max_outputs} critical cone target(s) with cone-local Yosys/ABC, then applied cleanup",
                allow_cone_yosys=True,
            )
            result["fallback_reason"] = str(exc)
            return result

    return _optimize_design_depth_locally(
        design,
        max_depth=max_depth,
        max_outputs=effective_max_outputs,
        engine="adaptive_topk_critical_cones",
        bounded_reason=f"adaptive top-K selected {effective_max_outputs} critical cone target(s), attempted cone-local Yosys/ABC, then applied cleanup",
        allow_cone_yosys=True,
    )


def replace_xnor_nor_with_basic_gates(design: Design) -> dict:
    """Backward-compatible alias for XNOR-to-NOR-only remapping."""
    return replace_xnor_with_nor(design)


@_rebuild_graph_after_transform
def replace_xnor_with_nor(design: Design) -> dict:
    """Rewrite each 2-input XNOR as a four-NOR implementation."""
    changed: list[dict[str, Any]] = []
    names = _CachedNameAllocator(design)
    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None or gate.type != "xnor" or len(gate.inputs) != 2:
            continue
        input_a, input_b = gate.inputs
        out_net = gate.output

        nor_ab = names.wire(f"{name}_nor_ab")
        nor_a = names.wire(f"{name}_nor_a")
        nor_b = names.wire(f"{name}_nor_b")
        nor_a_name = names.gate_name(f"{name}_nor_a")
        nor_b_name = names.gate_name(f"{name}_nor_b")
        nor_out_name = names.gate_name(f"{name}_nor_out")

        gate.type = "nor"
        gate.inputs = [input_a, input_b]
        gate.output = nor_ab
        design.add_gate(Gate(name=nor_a_name, type="nor", inputs=[input_a, nor_ab], output=nor_a))
        design.add_gate(Gate(name=nor_b_name, type="nor", inputs=[input_b, nor_ab], output=nor_b))
        design.add_gate(Gate(name=nor_out_name, type="nor", inputs=[nor_a, nor_b], output=out_net))
        changed.append(
            {
                "rewritten_gate": name,
                "old_type": "xnor",
                "added_gates": [nor_a_name, nor_b_name, nor_out_name],
                "added_nets": [nor_ab, nor_a, nor_b],
                "output": out_net,
            }
        )
    return {
        "changed": changed,
        "num_changed": len(changed),
        "added_gate_counts": {"nor": 3 * len(changed)},
        "rewritten_gate_counts": {"xnor": len(changed), "nor": len(changed)},
    }


@_rebuild_graph_after_transform
def replace_xor_with_nand(design: Design) -> dict:
    """Rewrite each 2-input XOR as the standard four-NAND implementation."""
    changed: list[dict[str, Any]] = []
    names = _CachedNameAllocator(design)
    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None or gate.type != "xor" or len(gate.inputs) != 2:
            continue
        input_a, input_b = gate.inputs
        out_net = gate.output

        nand_ab = names.wire(f"{name}_nand_ab")
        nand_a = names.wire(f"{name}_nand_a")
        nand_b = names.wire(f"{name}_nand_b")
        nand_a_name = names.gate_name(f"{name}_nand_a")
        nand_b_name = names.gate_name(f"{name}_nand_b")
        nand_out_name = names.gate_name(f"{name}_nand_out")

        gate.type = "nand"
        gate.inputs = [input_a, input_b]
        gate.output = nand_ab
        design.add_gate(Gate(name=nand_a_name, type="nand", inputs=[input_a, nand_ab], output=nand_a))
        design.add_gate(Gate(name=nand_b_name, type="nand", inputs=[input_b, nand_ab], output=nand_b))
        design.add_gate(Gate(name=nand_out_name, type="nand", inputs=[nand_a, nand_b], output=out_net))
        changed.append(
            {
                "rewritten_gate": name,
                "old_type": "xor",
                "added_gates": [nand_a_name, nand_b_name, nand_out_name],
                "added_nets": [nand_ab, nand_a, nand_b],
                "output": out_net,
            }
        )
    return {
        "changed": changed,
        "num_changed": len(changed),
        "added_gate_counts": {"nand": 3 * len(changed)},
        "rewritten_gate_counts": {"xor": len(changed), "nand": len(changed)},
    }


@_rebuild_graph_after_transform
def replace_and_not_with_nand(design: Design) -> dict:
    """Rewrite supported primitive gates into equivalent NAND/NOT structures."""
    changed: list[dict[str, Any]] = []
    added_gate_counts = {"nand": 0, "not": 0}
    rewritten_gate_counts: dict[str, int] = {}
    names = _CachedNameAllocator(design)

    def note(old_type: str) -> None:
        rewritten_gate_counts[old_type] = rewritten_gate_counts.get(old_type, 0) + 1

    def add_gate(gate_type: str, base: str, inputs: list[str], output: str | None = None) -> tuple[str, str]:
        gate_output = output or names.wire(base)
        gate_name = names.gate_name(base)
        design.add_gate(Gate(name=gate_name, type=gate_type, inputs=inputs, output=gate_output))
        added_gate_counts[gate_type] += 1
        return gate_name, gate_output

    def add_not(base: str, source: str, output: str | None = None) -> tuple[str, str]:
        return add_gate("not", f"{base}_not", [source], output)

    def add_nand(base: str, input_a: str, input_b: str, output: str | None = None) -> tuple[str, str]:
        return add_gate("nand", f"{base}_nand", [input_a, input_b], output)

    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None:
            continue
        old_type = gate.type
        inputs = list(gate.inputs)
        out_net = gate.output
        added_gates: list[str] = []
        added_nets: list[str] = []

        if old_type in {"nand", "not"}:
            continue
        if old_type == "and" and len(inputs) == 2:
            mid_net = names.wire(f"{name}_nand_pre")
            gate.type = "nand"
            gate.output = mid_net
            not_name, _ = add_not(f"{name}_restore", mid_net, out_net)
            added_gates.append(not_name)
            added_nets.append(mid_net)
        elif old_type == "or" and len(inputs) == 2:
            not_a_name, not_a = add_not(f"{name}_a", inputs[0])
            not_b_name, not_b = add_not(f"{name}_b", inputs[1])
            gate.type = "nand"
            gate.inputs = [not_a, not_b]
            added_gates.extend([not_a_name, not_b_name])
            added_nets.extend([not_a, not_b])
        elif old_type == "nor" and len(inputs) == 2:
            not_a_name, not_a = add_not(f"{name}_a", inputs[0])
            not_b_name, not_b = add_not(f"{name}_b", inputs[1])
            mid_net = names.wire(f"{name}_nand_or")
            gate.type = "nand"
            gate.inputs = [not_a, not_b]
            gate.output = mid_net
            restore_name, _ = add_not(f"{name}_restore", mid_net, out_net)
            added_gates.extend([not_a_name, not_b_name, restore_name])
            added_nets.extend([not_a, not_b, mid_net])
        elif old_type == "xor" and len(inputs) == 2:
            input_a, input_b = inputs
            nand_ab = names.wire(f"{name}_nand_ab")
            gate.type = "nand"
            gate.inputs = [input_a, input_b]
            gate.output = nand_ab
            nand_a_name, nand_a = add_nand(f"{name}_a", input_a, nand_ab)
            nand_b_name, nand_b = add_nand(f"{name}_b", input_b, nand_ab)
            nand_out_name, _ = add_nand(f"{name}_out", nand_a, nand_b, out_net)
            added_gates.extend([nand_a_name, nand_b_name, nand_out_name])
            added_nets.extend([nand_ab, nand_a, nand_b])
        elif old_type == "xnor" and len(inputs) == 2:
            input_a, input_b = inputs
            nand_ab = names.wire(f"{name}_nand_ab")
            gate.type = "nand"
            gate.inputs = [input_a, input_b]
            gate.output = nand_ab
            nand_a_name, nand_a = add_nand(f"{name}_a", input_a, nand_ab)
            nand_b_name, nand_b = add_nand(f"{name}_b", input_b, nand_ab)
            xor_net = names.wire(f"{name}_xor")
            nand_out_name, _ = add_nand(f"{name}_out", nand_a, nand_b, xor_net)
            not_name, _ = add_not(f"{name}_restore", xor_net, out_net)
            added_gates.extend([nand_a_name, nand_b_name, nand_out_name, not_name])
            added_nets.extend([nand_ab, nand_a, nand_b, xor_net])
        elif old_type == "buf" and len(inputs) == 1:
            mid_net = names.wire(f"{name}_not")
            gate.type = "not"
            gate.output = mid_net
            restore_name, _ = add_not(f"{name}_restore", mid_net, out_net)
            added_gates.append(restore_name)
            added_nets.append(mid_net)
        else:
            continue

        note(old_type)
        changed.append(
            {
                "rewritten_gate": name,
                "old_type": old_type,
                "new_type": gate.type,
                "added_gates": added_gates,
                "added_nets": added_nets,
                "output": out_net,
            }
        )
    return {
        "changed": changed,
        "num_changed": len(changed),
        "added_gate_counts": added_gate_counts,
        "rewritten_gate_counts": rewritten_gate_counts,
    }


@_rebuild_graph_after_transform
def replace_with_and_not(design: Design) -> dict:
    """Rewrite primitive combinational gates into an equivalent AND/NOT network."""
    changed: list[dict[str, Any]] = []
    added_gate_counts = {"and": 0, "not": 0}
    names = _CachedNameAllocator(design)

    def add_not(base: str, source: str) -> str:
        output = names.wire(f"{base}_not")
        name = names.gate_name(f"{base}_not")
        design.add_gate(Gate(name=name, type="not", inputs=[source], output=output))
        added_gate_counts["not"] += 1
        return output

    def add_and(base: str, input_a: str, input_b: str) -> str:
        output = names.wire(f"{base}_and")
        name = names.gate_name(f"{base}_and")
        design.add_gate(Gate(name=name, type="and", inputs=[input_a, input_b], output=output))
        added_gate_counts["and"] += 1
        return output

    def add_restore_not(base: str, source: str, output: str) -> None:
        name = names.gate_name(f"{base}_restore_not")
        design.add_gate(Gate(name=name, type="not", inputs=[source], output=output))
        added_gate_counts["not"] += 1

    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None:
            continue
        old_type = gate.type
        old_output = gate.output
        inputs = list(gate.inputs)

        if old_type in {"and", "not"}:
            continue
        if old_type == "buf" and len(inputs) == 1:
            gate.type = "and"
            gate.inputs = [inputs[0], inputs[0]]
        elif old_type == "nand" and len(inputs) == 2:
            mid = names.wire(f"{name}_and")
            gate.type = "and"
            gate.inputs = inputs
            gate.output = mid
            add_restore_not(name, mid, old_output)
        elif old_type == "or" and len(inputs) == 2:
            not_a = add_not(f"{name}_a", inputs[0])
            not_b = add_not(f"{name}_b", inputs[1])
            mid = names.wire(f"{name}_and")
            gate.type = "and"
            gate.inputs = [not_a, not_b]
            gate.output = mid
            add_restore_not(name, mid, old_output)
        elif old_type == "nor" and len(inputs) == 2:
            not_a = add_not(f"{name}_a", inputs[0])
            not_b = add_not(f"{name}_b", inputs[1])
            gate.type = "and"
            gate.inputs = [not_a, not_b]
        elif old_type == "xor" and len(inputs) == 2:
            not_a = add_not(f"{name}_a", inputs[0])
            not_b = add_not(f"{name}_b", inputs[1])
            a_and_not_b = add_and(f"{name}_a_not_b", inputs[0], not_b)
            not_a_and_b = add_and(f"{name}_not_a_b", not_a, inputs[1])
            not_term_a = add_not(f"{name}_term_a", a_and_not_b)
            not_term_b = add_not(f"{name}_term_b", not_a_and_b)
            mid = names.wire(f"{name}_and")
            gate.type = "and"
            gate.inputs = [not_term_a, not_term_b]
            gate.output = mid
            add_restore_not(name, mid, old_output)
        elif old_type == "xnor" and len(inputs) == 2:
            not_a = add_not(f"{name}_a", inputs[0])
            not_b = add_not(f"{name}_b", inputs[1])
            a_and_b = add_and(f"{name}_a_b", inputs[0], inputs[1])
            not_a_and_not_b = add_and(f"{name}_not_a_not_b", not_a, not_b)
            not_term_a = add_not(f"{name}_term_a", a_and_b)
            not_term_b = add_not(f"{name}_term_b", not_a_and_not_b)
            mid = names.wire(f"{name}_and")
            gate.type = "and"
            gate.inputs = [not_term_a, not_term_b]
            gate.output = mid
            add_restore_not(name, mid, old_output)
        else:
            continue

        changed.append(
            {
                "rewritten_gate": name,
                "old_type": old_type,
                "new_type": gate.type,
                "output": old_output,
            }
        )

    return {
        "changed": changed,
        "num_changed": len(changed),
        "added_gate_counts": added_gate_counts,
    }


@_rebuild_graph_after_transform
def merge_equivalent_gates(design: Design) -> dict:
    """Merge structurally identical primitive gates when the duplicate output is internal."""
    rebuild_graph(design)
    canonical_by_key: dict[tuple[str, tuple[str, ...]], str] = {}
    changed: list[dict[str, Any]] = []
    commutative = {"and", "or", "nand", "nor", "xor", "xnor"}

    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None:
            continue
        inputs = tuple(sorted(gate.inputs)) if gate.type in commutative else tuple(gate.inputs)
        key = (gate.type, inputs)
        canonical_name = canonical_by_key.get(key)
        if canonical_name is None:
            canonical_by_key[key] = name
            continue
        canonical = design.gates.get(canonical_name)
        if canonical is None or gate.output in design.outputs:
            continue
        for sink in list(design.fanouts.get(gate.output, [])):
            if sink.startswith("PO:"):
                break
        else:
            for sink in list(design.fanouts.get(gate.output, [])):
                _redirect_sink(design, sink, old_net=gate.output, new_net=canonical.output)
            removed_output = gate.output
            del design.gates[name]
            _discard_internal_wire(design, removed_output)
            changed.append({"removed_gate": name, "canonical_gate": canonical_name, "redirected_net": removed_output, "replacement_net": canonical.output})
            rebuild_graph(design)

    return {"changed": changed, "num_merged": len(changed)}


def _collapse_inverter_chains_once(design: Design) -> tuple[list[dict[str, Any]], int]:
    not_gates = {
        name: gate
        for name, gate in design.gates.items()
        if gate.type == "not" and len(gate.inputs) == 1
    }
    next_gate: dict[str, str] = {}
    previous_gate: dict[str, str] = {}

    for first_name, first in not_gates.items():
        if design.drivers.get(first.output) != f"GATE:{first_name}":
            continue
        sinks = design.fanouts.get(first.output, [])
        if len(sinks) != 1 or not sinks[0].startswith("GATE:"):
            continue
        second_name = sinks[0].split(":", 1)[1]
        second = not_gates.get(second_name)
        if second is None or second.inputs != [first.output]:
            continue
        next_gate[first_name] = second_name
        previous_gate[second_name] = first_name

    changes: list[dict[str, Any]] = []
    num_collapsed_pairs = 0
    for start in sorted(name for name in next_gate if name not in previous_gate):
        chain = [start]
        seen = {start}
        current = start
        while current in next_gate:
            following = next_gate[current]
            if following in seen:
                chain = []
                break
            chain.append(following)
            seen.add(following)
            current = following
        if len(chain) < 2:
            continue
        if not _is_current_inverter_chain(design, chain):
            continue

        change = _collapse_inverter_chain(design, chain)
        changes.append(change)
        num_collapsed_pairs += int(change["num_collapsed_pairs"])
        rebuild_graph(design)

    return changes, num_collapsed_pairs


def _is_current_inverter_chain(design: Design, chain: list[str]) -> bool:
    """Return True when a precomputed inverter chain still matches the graph."""
    if len(chain) < 2:
        return False
    gates = []
    for name in chain:
        gate = design.gates.get(name)
        if gate is None or gate.type != "not" or len(gate.inputs) != 1:
            return False
        gates.append(gate)

    for first, second in zip(gates, gates[1:]):
        if design.drivers.get(first.output) != f"GATE:{first.name}":
            return False
        if second.inputs != [first.output]:
            return False
        if f"GATE:{second.name}" not in design.fanouts.get(first.output, []):
            return False

    return True


def _collapse_inverter_chain(design: Design, chain: list[str]) -> dict[str, Any]:
    gates = [design.gates[name] for name in chain]
    source_net = gates[0].inputs[0]
    terminal = gates[-1]
    terminal_output = terminal.output
    num_collapsed_pairs = len(chain) // 2

    if len(chain) % 2 == 1:
        removed_names = chain[:-1]
        removed_nets = [gate.output for gate in gates[:-1]]
        terminal.inputs = [source_net]
        for name in removed_names:
            del design.gates[name]
        for net in removed_nets:
            _discard_internal_wire(design, net)
        return {
            "rule": "collapse_inverter_chain_to_not",
            "chain": chain,
            "num_collapsed_pairs": num_collapsed_pairs,
            "removed_gates": removed_names,
            "rewritten_gate": terminal.name,
            "replacement_net": source_net,
            "removed_nets": removed_nets,
        }

    if _must_keep_output_driver(design, terminal_output):
        removed_names = chain[:-1]
        removed_nets = [gate.output for gate in gates[:-1]]
        terminal.type = "buf"
        terminal.inputs = [source_net]
        for name in removed_names:
            del design.gates[name]
        for net in removed_nets:
            _discard_internal_wire(design, net)
        return {
            "rule": "collapse_inverter_chain_to_output_buffer",
            "chain": chain,
            "num_collapsed_pairs": num_collapsed_pairs,
            "removed_gates": removed_names,
            "rewritten_gate": terminal.name,
            "replacement_net": source_net,
            "removed_nets": removed_nets,
        }

    for sink in list(design.fanouts.get(terminal_output, [])):
        _redirect_sink(design, sink, old_net=terminal_output, new_net=source_net)
    removed_nets = [gate.output for gate in gates]
    for name in chain:
        del design.gates[name]
    for net in removed_nets:
        _discard_internal_wire(design, net)
    return {
        "rule": "collapse_inverter_chain_to_wire",
        "chain": chain,
        "num_collapsed_pairs": num_collapsed_pairs,
        "removed_gates": chain,
        "redirected_net": terminal_output,
        "replacement_net": source_net,
        "removed_nets": removed_nets,
    }


def _redirect_sink(design: Design, sink: str, old_net: str, new_net: str) -> None:
    kind, name = sink.split(":", 1)
    if kind == "GATE":
        gate = design.gates.get(name)
        if gate is None:
            raise ValueError(f"Gate sink no longer exists: {name}")
        gate.inputs = [new_net if item == old_net else item for item in gate.inputs]
        design.wires.add(new_net)
        return
    if kind == "DFF":
        dff = design.dffs.get(name)
        if dff is None:
            raise ValueError(f"DFF sink no longer exists: {name}")
        _redirect_dff_input(dff, old_net, new_net)
        design.wires.add(new_net)
        return
    raise ValueError(f"Cannot redirect sink type for fanout buffering: {sink}")


def _redirect_dff_input(dff: DFF, old_net: str, new_net: str) -> None:
    if dff.d == old_net:
        dff.d = new_net
    if dff.clk == old_net:
        dff.clk = new_net
    if dff.rst == old_net:
        dff.rst = new_net


def _pin_to_input_index(pin: str) -> int:
    normalized = pin.strip().upper()
    aliases = {"A": 0, "IN": 0, "I": 0, "IN0": 0, "A1": 0, "B": 1, "IN1": 1, "A2": 1}
    if normalized in aliases:
        return aliases[normalized]
    if normalized.isdigit():
        return int(normalized)
    if normalized.startswith("IN") and normalized[2:].isdigit():
        return int(normalized[2:])
    raise ValueError(f'Unsupported input pin name: "{pin}"')


def _unique_from_used(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    index = 1
    while f"{base}_{index}" in used:
        index += 1
    name = f"{base}_{index}"
    used.add(name)
    return name


def _sanitize_unique_base(value: str, fallback: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_$]", "_", value.strip())
    if not sanitized:
        sanitized = fallback
    if not re.match(r"^[A-Za-z_]", sanitized):
        sanitized = f"{fallback}_{sanitized}"
    return sanitized


class _CachedNameAllocator:
    """Fast transform-local unique name allocator for bulk rewrites."""

    def __init__(self, design: Design) -> None:
        self.design = design
        self.used = set(design.inputs) | set(design.outputs) | set(design.wires) | set(design.gates) | set(design.dffs)

    def wire(self, base: str) -> str:
        name = _unique_from_used(_sanitize_unique_base(base, "n"), self.used)
        self.design.wires.add(name)
        return name

    def gate_name(self, base: str) -> str:
        return _unique_from_used(_sanitize_unique_base(base, "U"), self.used)


def _simplify_constant_gate(design: Design, gate_name: str) -> dict[str, Any] | None:
    gate = design.gates[gate_name]
    original_type = gate.type
    original_inputs = list(gate.inputs)
    replacement = _constant_gate_replacement(gate)
    if replacement is None:
        return None

    kind, value = replacement
    if kind == "direct":
        action = _replace_gate_output_with_direct_signal(design, gate_name, value)
    elif kind == "inverted":
        action = _replace_gate_output_with_inverter(design, gate_name, value)
    elif kind == "rewrite":
        new_type, inputs = value
        gate.type = new_type
        gate.inputs = inputs
        action = "rewritten_gate"
    else:
        raise ValueError(f"Unknown constant propagation replacement: {kind}")
    if action == "unchanged_output_driver":
        return None

    return {
        "gate": gate_name,
        "old_type": original_type,
        "old_inputs": original_inputs,
        "replacement": replacement,
        "action": action,
    }


def _constant_gate_replacement(gate: Gate) -> tuple[str, Any] | None:
    inputs = list(gate.inputs)
    if gate.type == "buf" and len(inputs) == 1:
        if is_constant(inputs[0]):
            return ("direct", _constant_value(inputs[0]))
        return None
    if gate.type == "not" and len(inputs) == 1:
        if is_constant(inputs[0]):
            return ("direct", _bool_constant(not _constant_bool(inputs[0])))
        return None
    if gate.type in {"and", "nand"}:
        return _and_like_replacement(gate)
    if gate.type in {"or", "nor"}:
        return _or_like_replacement(gate)
    if gate.type in {"xor", "xnor"}:
        return _xor_like_replacement(gate)
    return None


def _and_like_replacement(gate: Gate) -> tuple[str, Any] | None:
    is_nand = gate.type == "nand"
    inputs = _unique_preserving_order(gate.inputs)
    if any(_is_const_false(net) for net in inputs):
        return ("direct", _bool_constant(is_nand))

    reduced = [net for net in inputs if not _is_const_true(net)]
    if not reduced:
        return ("direct", _bool_constant(not is_nand))
    if len(reduced) == 1:
        return ("inverted" if is_nand else "direct", reduced[0])
    if reduced != gate.inputs:
        return ("rewrite", ("nand" if is_nand else "and", reduced))
    return None


def _or_like_replacement(gate: Gate) -> tuple[str, Any] | None:
    is_nor = gate.type == "nor"
    inputs = _unique_preserving_order(gate.inputs)
    if any(_is_const_true(net) for net in inputs):
        return ("direct", _bool_constant(not is_nor))

    reduced = [net for net in inputs if not _is_const_false(net)]
    if not reduced:
        return ("direct", _bool_constant(is_nor))
    if len(reduced) == 1:
        return ("inverted" if is_nor else "direct", reduced[0])
    if reduced != gate.inputs:
        return ("rewrite", ("nor" if is_nor else "or", reduced))
    return None


def _xor_like_replacement(gate: Gate) -> tuple[str, Any] | None:
    parity = 0
    counts: dict[str, int] = {}
    ordered: list[str] = []
    for net in gate.inputs:
        if _is_const_true(net):
            parity ^= 1
        elif _is_const_false(net):
            continue
        elif is_constant(net):
            return None
        else:
            if net not in counts:
                ordered.append(net)
            counts[net] = counts.get(net, 0) + 1

    reduced = [net for net in ordered if counts[net] % 2 == 1]
    inverted = (gate.type == "xnor") ^ bool(parity)
    if not reduced:
        return ("direct", _bool_constant(inverted))
    if len(reduced) == 1:
        return ("inverted" if inverted else "direct", reduced[0])

    new_type = "xnor" if inverted else "xor"
    if new_type != gate.type or reduced != gate.inputs:
        return ("rewrite", (new_type, reduced))
    return None


def _replace_gate_output_with_direct_signal(design: Design, gate_name: str, replacement_net: str) -> str:
    gate = design.gates[gate_name]
    output_net = gate.output
    if _must_keep_output_driver(design, output_net):
        if gate.type == "buf" and gate.inputs == [replacement_net]:
            return "unchanged_output_driver"
        gate.type = "buf"
        gate.inputs = [replacement_net]
        _add_reference_wire_if_needed(design, replacement_net)
        return "rewritten_output_driver"

    for sink in list(design.fanouts.get(output_net, [])):
        _redirect_sink(design, sink, old_net=output_net, new_net=replacement_net)
    del design.gates[gate_name]
    _discard_internal_wire(design, output_net)
    return "removed_gate"


def _replace_gate_output_with_inverter(design: Design, gate_name: str, input_net: str) -> str:
    gate = design.gates[gate_name]
    gate.type = "not"
    gate.inputs = [input_net]
    _add_reference_wire_if_needed(design, input_net)
    return "rewritten_as_inverter"


def _must_keep_output_driver(design: Design, output_net: str) -> bool:
    return output_net in design.outputs or any(
        sink.startswith("PO:") for sink in design.fanouts.get(output_net, [])
    )


def _add_reference_wire_if_needed(design: Design, net: str) -> None:
    if not is_constant(net) and net not in design.inputs and net not in design.outputs:
        design.wires.add(net)


def _unique_preserving_order(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _constant_value(net: str) -> str:
    return _bool_constant(_constant_bool(net))


def _constant_bool(net: str) -> bool:
    if net in {"1'b1", "1"}:
        return True
    if net in {"1'b0", "0"}:
        return False
    raise ValueError(f"Unsupported constant for propagation: {net}")


def _is_const_true(net: str) -> bool:
    return net in {"1'b1", "1"}


def _is_const_false(net: str) -> bool:
    return net in {"1'b0", "0"}


def _bool_constant(value: bool) -> str:
    return "1'b1" if value else "1'b0"


def _max_fanout(design: Design) -> int:
    rebuild_graph(design)
    return max((len(sinks) for sinks in design.fanouts.values()), default=0)


def _depths_from_source(design: Design, src: str, dsts: list[str]) -> dict[str, int]:
    depths: dict[str, int] = {}
    for dst in dsts:
        depth, path = max_depth(design, src, dst)
        if not path:
            raise ValueError(f'No combinational path found from "{src}" to "{dst}".')
        depths[dst] = depth
    return depths


def _require_independent_destinations(design: Design, dsts: list[str]) -> None:
    for dst in dsts:
        if dst not in design.all_nets():
            raise ValueError(f'Destination net not found: "{dst}"')
    for index, src_dst in enumerate(dsts):
        for other_dst in dsts[index + 1 :]:
            if find_path(design, src_dst, other_dst) or find_path(design, other_dst, src_dst):
                raise ValueError(
                    "balance_depth_with_buffers currently requires independent "
                    f'destinations, but "{src_dst}" and "{other_dst}" are connected.'
                )


def _insert_buffer_chain_before_net(design: Design, dst: str, count: int) -> dict[str, Any]:
    rebuild_graph(design)
    driver = design.drivers.get(dst)
    if driver is None:
        raise ValueError(f'Destination net "{dst}" has no driver.')
    if not driver.startswith("GATE:"):
        raise ValueError(
            f'Destination net "{dst}" is driven by {driver}; only gate-driven destinations are supported.'
        )

    driver_gate_name = driver.split(":", 1)[1]
    driver_gate = design.gates[driver_gate_name]
    driver_output = design.make_unique_wire_name(f"{dst}_balance_pre")
    driver_gate.output = driver_output
    design.wires.add(driver_output)

    added_gates: list[str] = []
    added_nets: list[str] = [driver_output]
    source_net = driver_output
    for index in range(count):
        output_net = dst if index == count - 1 else design.make_unique_wire_name(f"{dst}_balance")
        buffer_name = design.make_unique_gate_name(f"{dst}_balance_buf")
        design.add_gate(Gate(name=buffer_name, type="buf", inputs=[source_net], output=output_net))
        added_gates.append(buffer_name)
        if output_net != dst:
            added_nets.append(output_net)
        source_net = output_net

    return {
        "dst": dst,
        "old_driver": driver_gate_name,
        "added_gates": added_gates,
        "added_nets": added_nets,
    }


def _simplify_double_inverter_in_cone(design: Design, cone_gates: set[str]) -> dict[str, Any] | None:
    rebuild_graph(design)
    for second_name in sorted(cone_gates):
        second = design.gates.get(second_name)
        if second is None or second.type != "not" or len(second.inputs) != 1:
            continue

        mid_net = second.inputs[0]
        first_driver = design.drivers.get(mid_net)
        if not first_driver or not first_driver.startswith("GATE:"):
            continue
        first_name = first_driver.split(":", 1)[1]
        if first_name not in cone_gates:
            continue
        first = design.gates.get(first_name)
        if first is None or first.type != "not" or len(first.inputs) != 1:
            continue
        if design.fanouts.get(mid_net, []) != [f"GATE:{second_name}"]:
            continue

        source_net = first.inputs[0]
        output_net = second.output
        if output_net in design.outputs:
            second.type = "buf"
            second.inputs = [source_net]
            del design.gates[first_name]
            _discard_internal_wire(design, mid_net)
            return {
                "rule": "double_inverter_to_output_buffer",
                "removed_gates": [first_name],
                "rewritten_gate": second_name,
                "removed_nets": [mid_net],
            }

        sinks = list(design.fanouts.get(output_net, []))
        if any(sink.startswith("PO:") for sink in sinks):
            continue
        for sink in sinks:
            _redirect_sink(design, sink, old_net=output_net, new_net=source_net)
        del design.gates[first_name]
        del design.gates[second_name]
        _discard_internal_wire(design, mid_net)
        _discard_internal_wire(design, output_net)
        return {
            "rule": "remove_double_inverter",
            "removed_gates": [first_name, second_name],
            "redirected_net": output_net,
            "replacement_net": source_net,
            "removed_nets": [mid_net, output_net],
        }
    return None


def _remove_internal_buffer_in_cone(design: Design, cone_gates: set[str]) -> dict[str, Any] | None:
    rebuild_graph(design)
    for gate_name in sorted(cone_gates):
        gate = design.gates.get(gate_name)
        if gate is None or gate.type != "buf" or len(gate.inputs) != 1:
            continue
        output_net = gate.output
        if output_net in design.outputs:
            continue
        sinks = list(design.fanouts.get(output_net, []))
        if any(sink.startswith("PO:") for sink in sinks):
            continue

        replacement_net = gate.inputs[0]
        for sink in sinks:
            _redirect_sink(design, sink, old_net=output_net, new_net=replacement_net)
        del design.gates[gate_name]
        _discard_internal_wire(design, output_net)
        return {
            "rule": "remove_internal_buffer",
            "removed_gates": [gate_name],
            "redirected_net": output_net,
            "replacement_net": replacement_net,
            "removed_nets": [output_net],
        }
    return None


def _discard_internal_wire(design: Design, net: str) -> None:
    if net not in design.inputs and net not in design.outputs:
        design.wires.discard(net)


def _referenced_as_internal_net(design: Design, net: str) -> bool:
    if net in design.inputs or net in design.outputs or is_constant(net):
        return False
    for gate in design.gates.values():
        if gate.output == net or net in gate.inputs:
            return True
    for dff in design.dffs.values():
        if net in {dff.d, dff.q, dff.clk, dff.rst}:
            return True
    return False


def _adaptive_depth_topk(gate_count: int, has_fanout_buffers: bool) -> int:
    if gate_count <= 2000:
        k = 32
    elif gate_count <= 8000:
        k = 32
    elif gate_count <= 20000:
        k = 16
    else:
        k = 8
    if has_fanout_buffers:
        k = min(k, 8)
    return k


def _optimize_design_depth_with_yosys_abc(design: Design, max_depth: int | None = None) -> dict:
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)
    attempts: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        raw_input = tmp_dir / "input.v"
        yosys_input = tmp_dir / "input_for_abc.v"
        optimized_output = tmp_dir / "optimized.v"
        write_verilog(design, raw_input)
        _write_yosys_abc_input(raw_input, yosys_input)

        candidates: list[tuple[int, int, Design, dict[str, Any]]] = []
        for label, target_depth in _abc_candidate_depth_targets(initial_depth):
            script = _build_yosys_abc_depth_script(
                input_path=yosys_input,
                output_path=optimized_output,
                top_module=design.module_name,
                max_depth=target_depth,
                use_delay_target=target_depth is not None,
            )
            optimized, reason = _run_yosys_depth_attempt(
                script,
                tmp_dir=tmp_dir,
                output_path=optimized_output,
                timeout=_yosys_abc_timeout(initial_gate_count, cheap=False),
                label=f"full_yosys_abc_{label}",
            )
            if optimized is None:
                attempts.append(reason)
                continue
            final_depth = _design_max_depth(optimized)
            final_gate_count = len(optimized.gates)
            candidates.append(
                (
                    final_depth,
                    final_gate_count,
                    optimized,
                    {"attempt": label, "abc_target_depth": target_depth},
                )
            )

    if not candidates:
        raise RuntimeError("; ".join(attempts) or "Yosys/ABC failed without a detailed reason.")

    final_depth, final_gate_count, optimized, best_summary = min(candidates, key=lambda item: (item[0], item[1]))
    if final_depth > initial_depth:
        raise RuntimeError(
            "Yosys/ABC candidate increased structural depth "
            f"from {initial_depth} to {final_depth}."
        )
    if max_depth is None and final_depth == initial_depth and final_gate_count > initial_gate_count:
        raise RuntimeError(
            "Yosys/ABC candidate did not reduce structural depth and increased gate count "
            f"from {initial_gate_count} to {final_gate_count}."
        )

    _replace_design_contents(design, optimized)
    target_met = max_depth is None or final_depth <= max_depth
    return {
        "engine": "yosys_abc",
        "max_depth": max_depth,
        "max_outputs": None,
        "attempted_outputs": sorted(design.outputs),
        "skipped_outputs": [],
        "initial_gate_count": initial_gate_count,
        "final_gate_count": final_gate_count,
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "target_met": target_met,
        "abc_summary": best_summary,
        "changed": [],
        "num_changed_outputs": 0 if initial_depth == final_depth and initial_gate_count == len(design.gates) else len(design.outputs),
    }


def _run_yosys_depth_attempt(
    script: str,
    *,
    tmp_dir: Path,
    output_path: Path,
    timeout: float,
    label: str,
) -> tuple[Design | None, str]:
    if output_path.exists():
        output_path.unlink()
    completed = run_yosys_script(script, cwd=tmp_dir, timeout=timeout)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "unknown Yosys/ABC error").strip()
        return None, f"{label} failed: {message}"
    if not output_path.exists():
        return None, f"{label} failed: Yosys/ABC did not produce an optimized Verilog file."
    try:
        optimized = parse_verilog(output_path)
        _canonicalize_yosys_generated_names(optimized)
        _prune_unreferenced_wires(optimized)
        connectivity = check_connectivity(optimized)
    except Exception as exc:
        return None, f"{label} failed while parsing/checking candidate: {exc}"
    if not connectivity.get("ok"):
        return None, f"{label} failed connectivity check: {_summarize_connectivity_failure(connectivity)}"
    return optimized, ""


def _canonicalize_yosys_generated_names(design: Design) -> None:
    """Rename Yosys/temp-derived internal names to stable contest-style names."""
    used = set(design.inputs) | set(design.outputs) | set(design.wires) | set(design.gates) | set(design.dffs)

    gate_renames: dict[str, str] = {}
    gate_index = _next_numeric_suffix("g", used)
    for old_name in sorted(design.gates):
        if not _is_yosys_generated_name(old_name):
            continue
        new_name, gate_index = _allocate_numeric_name("g", gate_index, used)
        gate_renames[old_name] = new_name

    for old_name, new_name in gate_renames.items():
        gate = design.gates.pop(old_name)
        gate.name = new_name
        design.gates[new_name] = gate

    net_renames: dict[str, str] = {}
    net_index = _next_numeric_suffix("n", used)
    protected_nets = set(design.inputs) | set(design.outputs)
    for old_net in sorted(design.wires):
        if old_net in protected_nets or is_constant(old_net):
            continue
        if not _is_yosys_generated_name(old_net):
            continue
        new_net, net_index = _allocate_numeric_name("n", net_index, used)
        net_renames[old_net] = new_net

    if net_renames:
        for gate in design.gates.values():
            gate.inputs = [net_renames.get(net, net) for net in gate.inputs]
            gate.output = net_renames.get(gate.output, gate.output)
        for dff in design.dffs.values():
            dff.d = net_renames.get(dff.d, dff.d)
            dff.q = net_renames.get(dff.q, dff.q)
            if dff.clk:
                dff.clk = net_renames.get(dff.clk, dff.clk)
            if dff.rst:
                dff.rst = net_renames.get(dff.rst, dff.rst)
        design.wires = {net_renames.get(net, net) for net in design.wires}

    if gate_renames or net_renames:
        rebuild_graph(design)


def _is_yosys_generated_name(name: str) -> bool:
    low = name.lower()
    if any(marker in low for marker in ("input_for_yosys", "yosys_input", "cone_yosys", "tmp_tmp", "_tmp_", "$")):
        return True
    if low.startswith("_yosys_bit_"):
        return True
    if re.fullmatch(r"_\d+_", name):
        return True
    if re.fullmatch(r"g\d+\.(?:d|q|clk|rst)", name):
        return True
    return False


def _next_numeric_suffix(prefix: str, used: set[str]) -> int:
    max_suffix = 0
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)$")
    for name in used:
        match = pattern.fullmatch(name)
        if match:
            max_suffix = max(max_suffix, int(match.group(1)))
    return max_suffix + 1


def _allocate_numeric_name(prefix: str, start: int, used: set[str]) -> tuple[str, int]:
    index = start
    while True:
        candidate = f"{prefix}{index}"
        index += 1
        if candidate in used:
            continue
        used.add(candidate)
        return candidate, index


def _yosys_abc_timeout(gate_count: int, *, cheap: bool) -> float:
    if cheap:
        if gate_count <= 2000:
            return 30.0
        if gate_count <= 8000:
            return 45.0
        return 60.0
    if gate_count <= 2000:
        return 90.0
    if gate_count <= 8000:
        return 120.0
    return 150.0


def _summarize_connectivity_failure(connectivity: dict[str, Any]) -> str:
    missing = connectivity.get("missing_drivers") or []
    duplicates = connectivity.get("duplicate_drivers") or {}
    pieces: list[str] = []
    if missing:
        sample = ", ".join(str(item) for item in missing[:8])
        suffix = "" if len(missing) <= 8 else f", +{len(missing) - 8} more"
        pieces.append(f"missing_drivers=[{sample}{suffix}]")
    if duplicates:
        sample_items = list(duplicates.items())[:4]
        sample = ", ".join(f"{net}: {drivers}" for net, drivers in sample_items)
        suffix = "" if len(duplicates) <= 4 else f", +{len(duplicates) - 4} more"
        pieces.append(f"duplicate_drivers={{{sample}{suffix}}}")
    return "; ".join(pieces) or str(connectivity)

def _optimize_design_depth_locally(
    design: Design,
    max_depth: int | None = None,
    max_outputs: int | None = None,
    engine: str = "local",
    bounded_reason: str | None = None,
    allow_cone_yosys: bool = False,
) -> dict:
    expensive_depth = len(design.gates) > 10000
    if expensive_depth:
        return _optimize_design_depth_large_topk_cleanup(
            design,
            max_depth=max_depth,
            max_outputs=max_outputs or _adaptive_depth_topk(len(design.gates), False),
            engine=engine,
            bounded_reason=bounded_reason,
        )

    reports = _optimization_target_cone_sizes(design)
    outputs = [name for name, _ in sorted(reports.items(), key=lambda item: (item[1].get("depth", 0), item[1].get("num_gates", 0), item[0]), reverse=True)]
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)

    selected_outputs = outputs[:max_outputs] if max_outputs is not None else outputs
    changed: list[dict[str, Any]] = []
    skipped_dynamic: list[dict[str, str]] = []

    for output in selected_outputs:
        if output not in design.all_nets():
            skipped_dynamic.append({"target": output, "reason": "target no longer exists after earlier local rewrites"})
            continue
        cone_size = reports.get(output, {}).get("num_gates", 0)
        if len(design.gates) > 10000 and cone_size > 2000:
            skipped_dynamic.append({"target": output, "reason": f"skipped large cone with {cone_size} gate(s)"})
            continue
        try:
            result = optimize_cone(
                design,
                output,
                max_depth=max_depth,
                minimize_gate_count=True,
                allow_yosys_abc=allow_cone_yosys,
            )
        except ValueError as exc:
            skipped_dynamic.append({"target": output, "reason": str(exc)})
            continue
        if result.get("num_changed", 0):
            changed.append(result)

    final_depth = _design_max_depth(design) if not expensive_depth else None
    result = {
        "engine": engine,
        "max_depth": max_depth,
        "max_outputs": max_outputs,
        "attempted_outputs": selected_outputs,
        "skipped_outputs": outputs[len(selected_outputs):] + skipped_dynamic,
        "initial_gate_count": initial_gate_count,
        "final_gate_count": len(design.gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "target_met": max_depth is None or final_depth <= max_depth,
        "changed": changed,
        "num_changed_outputs": len(changed),
    }
    if bounded_reason is not None:
        result["bounded_reason"] = bounded_reason
    return result


def _optimize_design_depth_large_topk_cleanup(
    design: Design,
    max_depth: int | None,
    max_outputs: int,
    engine: str,
    bounded_reason: str | None,
) -> dict:
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)
    targets = _large_design_depth_targets(design, max_outputs)
    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for target in targets:
        if target not in design.all_nets():
            skipped.append({"target": target, "reason": "target no longer exists after earlier rewrites"})
            continue
        try:
            resolved_target = _resolve_optimization_cone_target(design, target)["target"]
            cone_size = len(logic_cone(design, resolved_target))
        except ValueError as exc:
            skipped.append({"target": target, "reason": str(exc)})
            continue
        if cone_size > CONE_YOSYS_ABC_MAX_GATES:
            skipped.append(
                {
                    "target": target,
                    "reason": f"skipped large cone with {cone_size} gate(s); cone-local Yosys/ABC limit is {CONE_YOSYS_ABC_MAX_GATES}",
                }
            )
            continue
        try:
            result = optimize_cone(
                design,
                target,
                max_depth=max_depth,
                minimize_gate_count=True,
                allow_yosys_abc=True,
            )
        except (RuntimeError, ValueError) as exc:
            skipped.append({"target": target, "reason": str(exc)})
            continue
        if result.get("num_changed", 0):
            changed.append(result)
        else:
            skipped.append({"target": target, "reason": "cone-local Yosys/ABC and cleanup produced no accepted improvement"})

    cleanup_result = _optimize_design_depth_fast_cleanup(design, max_depth=max_depth)
    changed.extend(cleanup_result.get("changed", []))
    final_depth = _design_max_depth(design)
    cleanup_result.update(
        {
            "engine": engine,
            "max_outputs": max_outputs,
            "attempted_outputs": targets,
            "skipped_outputs": skipped,
            "initial_depth": initial_depth,
            "final_depth": final_depth,
            "target_met": max_depth is None or final_depth <= max_depth,
            "changed": changed,
            "num_changed_outputs": len(changed),
            "bounded_reason": bounded_reason or "large design: adaptive top-K selected targets, attempted cone-local Yosys/ABC, then applied cheap safe cleanup",
        }
    )
    cleanup_result["initial_gate_count"] = initial_gate_count
    cleanup_result["final_gate_count"] = len(design.gates)
    return cleanup_result


def _large_design_depth_targets(design: Design, max_outputs: int) -> list[str]:
    sinks = set(design.outputs)
    sinks.update(dff.d for dff in design.dffs.values() if not is_constant(dff.d))
    def score(net: str) -> tuple[int, int, str]:
        driver = design.drivers.get(net, "")
        driven_by_gate = 1 if driver.startswith("GATE:") else 0
        fanout = len(design.fanouts.get(net, []))
        return (-driven_by_gate, -fanout, net)
    return sorted((net for net in sinks if net in design.all_nets()), key=score)[:max_outputs]


def _optimize_design_depth_fast_cleanup(
    design: Design,
    max_depth: int | None,
) -> dict:
    initial_gate_count = len(design.gates)
    cleanup_results: list[dict[str, Any]] = []

    constant_result = constant_propagation(design, max_changes=128)
    if constant_result.get("num_changed", 0):
        cleanup_results.append({"op": "constant_propagation", **constant_result})

    inverter_result = collapse_back_to_back_inverters(design)
    if inverter_result.get("num_collapsed", 0):
        cleanup_results.append({"op": "collapse_back_to_back_inverters", **inverter_result})

    dangling_result = remove_dangling(design)
    if dangling_result.get("num_removed_gates", 0) or dangling_result.get("num_removed_dffs", 0):
        cleanup_results.append({"op": "remove_dangling", **dangling_result})

    return {
        "engine": "large_design_bounded_cleanup",
        "max_depth": max_depth,
        "max_outputs": 0,
        "attempted_outputs": [],
        "skipped_outputs": [],
        "initial_gate_count": initial_gate_count,
        "final_gate_count": len(design.gates),
        "initial_depth": None,
        "final_depth": None,
        "target_met": max_depth is None,
        "changed": cleanup_results,
        "num_changed_outputs": len(cleanup_results),
        "bounded_reason": "large design: skipped expensive depth enumeration and optimized only cheap safe rewrites",
    }


def _optimize_design_depth_with_bounded_cleanup(
    design: Design,
    max_depth: int | None,
    outputs: list[str],
    initial_gate_count: int,
    initial_depth: int,
) -> dict:
    cleanup_results: list[dict[str, Any]] = []

    constant_result = constant_propagation(design, max_changes=256)
    if constant_result.get("num_changed", 0):
        cleanup_results.append({"op": "constant_propagation", **constant_result})

    inverter_result = collapse_back_to_back_inverters(design)
    if inverter_result.get("num_collapsed", 0):
        cleanup_results.append({"op": "collapse_back_to_back_inverters", **inverter_result})

    dangling_result = remove_dangling(design)
    if dangling_result.get("num_removed_gates", 0) or dangling_result.get("num_removed_dffs", 0):
        cleanup_results.append({"op": "remove_dangling", **dangling_result})

    final_depth = _design_max_depth(design) if cleanup_results else initial_depth
    return {
        "engine": "local_bounded_cleanup",
        "max_depth": max_depth,
        "max_outputs": 0,
        "attempted_outputs": [],
        "skipped_outputs": [
            {"target": output, "reason": "bounded cleanup skipped expensive per-cone rewrite"}
            for output in outputs
        ],
        "initial_gate_count": initial_gate_count,
        "final_gate_count": len(design.gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "target_met": max_depth is None or final_depth <= max_depth,
        "changed": cleanup_results,
        "num_changed_outputs": len(cleanup_results),
        "bounded_reason": "large design or many sequential/output targets",
    }

def _write_yosys_abc_input(source_path: Path, target_path: Path) -> None:
    source_text = source_path.read_text(encoding="utf-8")
    rewritten_text, dff_wrappers = _rewrite_dffs_for_yosys_abc(source_text)
    prelude = _build_yosys_abc_dff_prelude(dff_wrappers)
    prefix = prelude + "\n" if prelude else ""
    target_path.write_text(prefix + rewritten_text, encoding="utf-8")


def _rewrite_dffs_for_yosys_abc(text: str) -> tuple[str, set[int]]:
    wrappers: set[int] = set()
    statements: list[str] = []
    dff_re = re.compile(r"^(dff[A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$", re.S | re.I)

    for raw_statement in text.split(";"):
        stripped = raw_statement.strip()
        if not stripped:
            statements.append(raw_statement)
            continue
        match = dff_re.match(stripped)
        if not match:
            statements.append(raw_statement)
            continue
        _, inst_name, pin_text = match.groups()
        pins = [pin.strip() for pin in pin_text.split(",") if pin.strip()]
        if len(pins) not in {3, 4}:
            raise ValueError(f"DFF {inst_name} expects q, d, clk[, rst]")
        wrapper_type = _dff_wrapper_name(len(pins))
        wrappers.add(len(pins))
        replacement = f"{wrapper_type} {inst_name}({', '.join(pins)})"
        prefix_len = len(raw_statement) - len(raw_statement.lstrip())
        suffix_len = len(raw_statement) - len(raw_statement.rstrip())
        statements.append(raw_statement[:prefix_len] + replacement + raw_statement[len(raw_statement) - suffix_len:])
    return ";".join(statements), wrappers


def _build_yosys_abc_dff_prelude(wrappers: set[int]) -> str:
    return "\n\n".join(_build_sequential_dff_wrapper(count) for count in sorted(wrappers))


def _build_sequential_dff_wrapper(pin_count: int) -> str:
    name = _dff_wrapper_name(pin_count)
    if pin_count == 3:
        return "\n".join(
            [
                f"module {name}(q, d, clk);",
                "output reg q;",
                "input d, clk;",
                "always @(posedge clk) q <= d;",
                "endmodule",
                "",
            ]
        )
    return "\n".join(
        [
            f"module {name}(q, d, clk, rst);",
            "output reg q;",
            "input d, clk, rst;",
            "always @(posedge clk or posedge rst)",
            "  if (rst) q <= 1'b0;",
            "  else q <= d;",
            "endmodule",
            "",
        ]
    )

def _build_yosys_abc_depth_script(
    input_path: Path,
    output_path: Path,
    top_module: str,
    max_depth: int | None,
    use_delay_target: bool,
) -> str:
    abc_pass = f"abc -D {max(1, max_depth or 1)}" if use_delay_target else "abc"
    return "\n".join(
        [
            f"read_verilog -sv -noopt {quote_yosys_path(input_path)}",
            f"hierarchy -check -top {top_module}",
            "proc",
            "flatten",
            "opt_clean",
            "opt -full",
            "techmap",
            "opt -full",
            abc_pass,
            "opt_clean",
            f"write_verilog -noattr -simple-lhs {quote_yosys_path(output_path)}",
        ]
    )



def _build_yosys_abc_cone_script(
    input_path: Path,
    output_path: Path,
    top_module: str,
    max_depth: int | None,
) -> str:
    abc_pass = f"abc -D {max(1, max_depth or 1)}" if max_depth is not None else "abc"
    return "\n".join(
        [
            f"read_verilog -sv -noopt {quote_yosys_path(input_path)}",
            f"hierarchy -check -top {top_module}",
            "proc",
            "flatten",
            "opt_clean",
            "opt -full",
            "techmap",
            "opt -full",
            abc_pass,
            "opt_clean",
            f"write_verilog -noattr -simple-lhs {quote_yosys_path(output_path)}",
        ]
    )

def _build_yosys_abc_cheap_depth_script(
    input_path: Path,
    output_path: Path,
    top_module: str,
) -> str:
    return "\n".join(
        [
            f"read_verilog -sv -noopt {quote_yosys_path(input_path)}",
            f"hierarchy -check -top {top_module}",
            "proc",
            "flatten",
            "opt_clean",
            "abc",
            "opt_clean",
            f"write_verilog -noattr -simple-lhs {quote_yosys_path(output_path)}",
        ]
    )

def _prune_unreferenced_wires(design: Design) -> None:
    referenced = set(design.inputs) | set(design.outputs)
    for gate in design.gates.values():
        referenced.add(gate.output)
        referenced.update(net for net in gate.inputs if not is_constant(net))
    for dff in design.dffs.values():
        referenced.add(dff.q)
        if not is_constant(dff.d):
            referenced.add(dff.d)
        if dff.clk:
            referenced.add(dff.clk)
        if dff.rst:
            referenced.add(dff.rst)
    design.wires = {net for net in design.wires if net in referenced}
    rebuild_graph(design)

def _replace_design_contents(target: Design, source: Design) -> None:
    target.module_name = source.module_name
    target.inputs = set(source.inputs)
    target.outputs = set(source.outputs)
    target.wires = set(source.wires)
    target.gates = dict(source.gates)
    target.dffs = dict(source.dffs)
    target.drivers = dict(source.drivers)
    target.fanouts = {net: list(sinks) for net, sinks in source.fanouts.items()}
    rebuild_graph(target)


def _design_max_depth(design: Design) -> int:
    profile = _fast_depth_profile(design)
    return profile["max_depth"]


def _depth_sources(design: Design) -> set[str]:
    return set(design.inputs) | {dff.q for dff in design.dffs.values()}


def _depth_sinks(design: Design) -> set[str]:
    return set(design.outputs) | {dff.d for dff in design.dffs.values() if not is_constant(dff.d)}


def _optimization_target_cone_sizes(design: Design) -> dict[str, dict[str, Any]]:
    profile = _fast_depth_profile(design)
    report: dict[str, dict[str, Any]] = {}
    for target, depth in profile["endpoint_depths"].items():
        report[target] = {
            "target": target,
            "depth": depth,
            "num_gates": 0,
            "num_nets": 0,
        }
    return report


def _cone_max_depth(design: Design, target: str) -> int:
    profile = _fast_depth_profile(design)
    return profile["net_depths"].get(target, 0)


def _fast_depth_profile(design: Design) -> dict[str, Any]:
    rebuild_graph(design)
    constants = {"1'b0", "1'b1", "0", "1"}
    sources = _depth_sources(design) | constants
    all_nets = design.all_nets() | constants
    net_depth: dict[str, int] = {net: 0 for net in sources}

    # Treat undriven non-constant nets as structural boundaries. Connectivity
    # checks report them elsewhere; depth estimation should stay bounded.
    for net in all_nets:
        driver = design.drivers.get(net)
        if driver is None or not driver.startswith("GATE:"):
            net_depth.setdefault(net, 0)

    gate_best_input: dict[str, int] = {}
    gate_remaining: dict[str, int] = {}
    ready: list[str] = []
    for gate_name, gate in design.gates.items():
        remaining = 0
        best = 0
        for input_net in gate.inputs:
            if input_net in net_depth:
                best = max(best, net_depth[input_net])
            else:
                remaining += 1
        gate_best_input[gate_name] = best
        gate_remaining[gate_name] = remaining
        if remaining == 0:
            ready.append(gate_name)

    processed: set[str] = set()
    while ready:
        gate_name = ready.pop()
        if gate_name in processed:
            continue
        processed.add(gate_name)
        gate = design.gates[gate_name]
        output_depth = gate_best_input[gate_name] + 1
        if output_depth <= net_depth.get(gate.output, -1):
            continue
        net_depth[gate.output] = output_depth
        for sink in design.fanouts.get(gate.output, []):
            if not sink.startswith("GATE:"):
                continue
            sink_gate_name = sink.split(":", 1)[1]
            if sink_gate_name in processed:
                continue
            gate_best_input[sink_gate_name] = max(gate_best_input.get(sink_gate_name, 0), output_depth)
            gate_remaining[sink_gate_name] = max(0, gate_remaining.get(sink_gate_name, 0) - 1)
            if gate_remaining[sink_gate_name] == 0:
                ready.append(sink_gate_name)

    endpoints = sorted(net for net in _depth_sinks(design) if net in net_depth)
    endpoint_depths = {net: net_depth[net] for net in endpoints}
    max_depth_value = max(endpoint_depths.values(), default=0)
    return {
        "net_depths": net_depth,
        "endpoint_depths": endpoint_depths,
        "max_depth": max_depth_value,
        "processed_gates": len(processed),
        "unprocessed_gates": len(design.gates) - len(processed),
    }


# Constraint-aware optimize_cone wrapper. Keep the original implementation for
# unconstrained calls, and add a small legalization flow for cone gate-library
# constraints passed by the LLM tool schema.
_optimize_cone_base = optimize_cone
_optimize_design_depth_base = optimize_design_depth


def optimize_cone(
    design: Design,
    target: str,
    max_depth: int | None = None,
    minimize_gate_count: bool = True,
    allow_yosys_abc: bool = True,
    allowed_gates: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict:
    allowed = _normalize_allowed_gates(allowed_gates)
    if not allowed:
        return _optimize_cone_base(
            design,
            target,
            max_depth=max_depth,
            minimize_gate_count=minimize_gate_count,
            allow_yosys_abc=allow_yosys_abc,
        )

    rebuild_graph(design)
    if target in design.outputs and not logic_cone(design, target):
        if max_depth is not None and max_depth < 0:
            raise ValueError("optimize_cone requires max_depth >= 0 when provided.")
        return {
            "target": target,
            "resolved_target": target,
            "target_resolution": {
                "target": target,
                "original_target": target,
                "kind": "primary_output_empty_cone",
                "reason": "primary output has no combinational fanin cone to restructure",
            },
            "engine": "constraint_aware_cone",
            "allowed_gates": sorted(allowed),
            "max_depth": max_depth,
            "initial_gate_count": 0,
            "final_gate_count": 0,
            "removed_gate_count": 0,
            "initial_depth": 0,
            "final_depth": 0,
            "target_met": True,
            "changed": [],
            "num_changed": 0,
        }

    resolved = _resolve_optimization_cone_target(design, target)
    resolved_target = resolved["target"]
    initial_gates = logic_cone(design, resolved_target)
    initial_depth = _cone_max_depth(design, resolved_target)
    changed: list[dict[str, Any]] = []

    legalize = _legalize_cone_to_gate_library(design, resolved_target, allowed)
    if legalize["num_changed"]:
        changed.append({"rule": "legalize_cone", **legalize})
    cleanup = _optimize_cone_base(
        design,
        target,
        max_depth=None,
        minimize_gate_count=minimize_gate_count,
        allow_yosys_abc=False,
    )
    changed.extend(cleanup.get("changed", []))

    before_yosys = deepcopy(design)
    before_yosys_depth = _cone_max_depth(design, resolved_target)
    if allow_yosys_abc:
        yosys_result = _try_yosys_abc_for_small_cone(
            design,
            target=target,
            resolved_target=resolved_target,
            max_depth=max_depth,
            initial_gate_count=len(initial_gates),
            initial_depth=initial_depth,
            current_gate_count=len(logic_cone(design, resolved_target)),
            current_depth=before_yosys_depth,
        )
        if yosys_result is not None:
            relegalize = _legalize_cone_to_gate_library(design, resolved_target, allowed)
            if relegalize["num_changed"]:
                yosys_result.setdefault("changed", []).append({"rule": "post_yosys_relegalize", **relegalize})
            if _cone_uses_only_gates(design, resolved_target, allowed):
                changed.extend(yosys_result.get("changed", []))
            else:
                _replace_design_contents(design, before_yosys)

    if not _cone_uses_only_gates(design, resolved_target, allowed):
        raise RuntimeError(f'Constrained cone optimization failed to satisfy allowed_gates={sorted(allowed)}.')

    final_depth = _cone_max_depth(design, resolved_target)
    if max_depth is not None and final_depth > max_depth:
        raise ValueError(f'Optimized cone depth {final_depth} exceeds max_depth {max_depth} for target "{target}".')
    final_gates = logic_cone(design, resolved_target)
    return {
        "target": target,
        "resolved_target": resolved_target,
        "target_resolution": resolved,
        "engine": "constraint_aware_cone",
        "allowed_gates": sorted(allowed),
        "max_depth": max_depth,
        "initial_gate_count": len(initial_gates),
        "final_gate_count": len(final_gates),
        "removed_gate_count": len(initial_gates) - len(final_gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "target_met": max_depth is None or final_depth <= max_depth,
        "changed": changed,
        "num_changed": len(changed),
    }


def optimize_design_depth(
    design: Design,
    max_depth: int | None = None,
    max_outputs: int | None = None,
    allowed_gates: list[str] | set[str] | tuple[str, ...] | None = None,
    cost_function: str | None = None,
    cost_scope: str | None = None,
    constraints: list[dict[str, Any]] | None = None,
) -> dict:
    allowed = _normalize_allowed_gates(allowed_gates)
    cone_constraints = _normalize_cone_gate_constraints(constraints)
    if not cone_constraints:
        del cost_function, cost_scope
        if allowed:
            return _optimize_design_depth_with_allowed_gates(
                design,
                max_depth=max_depth,
                max_outputs=max_outputs,
                allowed=allowed,
            )
        return _optimize_design_depth_base(design, max_depth=max_depth, max_outputs=max_outputs)
    return _optimize_design_depth_with_constraints(
        design,
        max_depth=max_depth,
        max_outputs=max_outputs,
        cost_function=cost_function or "max_logic_depth",
        cost_scope=cost_scope or "whole_design",
        constraints=cone_constraints,
    )


def _optimize_design_depth_with_allowed_gates(
    design: Design,
    *,
    max_depth: int | None,
    max_outputs: int | None,
    allowed: set[str],
) -> dict[str, Any]:
    if allowed not in ({"and", "or", "not"}, {"and", "not"}, {"nand", "not"}, {"nor", "not"}):
        raise ValueError(f"Unsupported whole-design allowed_gates for depth optimization: {sorted(allowed)}")

    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)
    before_report = _design_gate_library_report(design, allowed)
    legalization_changes: list[dict[str, Any]] = []
    initial_legalize = _legalize_design_to_gate_library(design, allowed)
    if initial_legalize["num_changed"]:
        legalization_changes.append({"rule": "initial_whole_design_legalize", **initial_legalize})
    legalized_gate_count = len(design.gates)
    legalized_depth = _design_max_depth(design)

    best = deepcopy(design)
    best_depth = legalized_depth
    best_gate_count = legalized_gate_count
    best_result: dict[str, Any] | None = None
    accepted_passes: list[dict[str, Any]] = []
    rejected_passes: list[dict[str, Any]] = []
    pass_count = 1

    for pass_index in range(1, pass_count + 1):
        candidate = deepcopy(best)
        effective_max_outputs = max_outputs
        if effective_max_outputs is None and len(candidate.gates) > 10000:
            effective_max_outputs = _adaptive_depth_topk(
                len(candidate.gates),
                any("__fanout_buf_" in net for net in candidate.all_nets()),
            )
        try:
            candidate_result = _optimize_design_depth_base(
                candidate,
                max_depth=max_depth,
                max_outputs=effective_max_outputs,
            )
        except Exception as exc:
            rejected_passes.append({"pass": pass_index, "reason": str(exc)})
            break

        relegalize = _legalize_design_to_gate_library(candidate, allowed)
        report = _design_gate_library_report(candidate, allowed)
        candidate_depth = _design_max_depth(candidate)
        candidate_gate_count = len(candidate.gates)
        if not report["satisfied"]:
            rejected_passes.append(
                {
                    "pass": pass_index,
                    "reason": "depth candidate violated whole-design gate-library constraint after re-legalization",
                    "report": report,
                }
            )
            break
        if candidate_depth > best_depth:
            rejected_passes.append(
                {
                    "pass": pass_index,
                    "reason": f"candidate increased post-legalization depth from {best_depth} to {candidate_depth}",
                }
            )
            break
        if candidate_depth == best_depth and candidate_gate_count >= best_gate_count:
            rejected_passes.append(
                {
                    "pass": pass_index,
                    "reason": f"candidate kept depth {candidate_depth} and did not reduce gate count ({best_gate_count} -> {candidate_gate_count})",
                }
            )
            break

        pass_summary = {
            "pass": pass_index,
            "candidate_engine": candidate_result.get("engine"),
            "candidate_max_outputs": effective_max_outputs,
            "candidate_depth": candidate_depth,
            "candidate_gate_count": candidate_gate_count,
            "relegalized_gates": relegalize["num_changed"],
        }
        accepted_passes.append(pass_summary)
        if relegalize["num_changed"]:
            candidate_result.setdefault("changed", []).append({"rule": "post_depth_whole_design_relegalize", **relegalize})
        best = candidate
        best_depth = candidate_depth
        best_gate_count = candidate_gate_count
        best_result = candidate_result

    _replace_design_contents(design, best)
    final_report = _design_gate_library_report(design, allowed)
    if not final_report["satisfied"]:
        raise RuntimeError(f"Whole-design depth optimization failed to satisfy allowed_gates={sorted(allowed)}.")

    final_depth = _design_max_depth(design)
    final_gate_count = len(design.gates)
    return {
        "engine": "library_preserving_depth",
        "allowed_gates": sorted(allowed),
        "max_depth": max_depth,
        "max_outputs": max_outputs,
        "initial_gate_count": initial_gate_count,
        "legalized_gate_count": legalized_gate_count,
        "final_gate_count": final_gate_count,
        "initial_depth": initial_depth,
        "legalized_depth": legalized_depth,
        "final_depth": final_depth,
        "target_met": max_depth is None or final_depth <= max_depth,
        "gate_library_report_before": before_report,
        "gate_library_report_after": final_report,
        "legalization_changes": legalization_changes,
        "candidate_engine": best_result.get("engine") if isinstance(best_result, dict) else None,
        "candidate_applied": bool(accepted_passes),
        "accepted_passes": accepted_passes,
        "rejected_passes": rejected_passes,
        "attempted_outputs": best_result.get("attempted_outputs", []) if isinstance(best_result, dict) else [],
        "skipped_outputs": best_result.get("skipped_outputs", []) if isinstance(best_result, dict) else [],
        "bounded_reason": (
            f"whole-design allowed_gates={sorted(allowed)}; "
            f"legalized first, then ran {len(accepted_passes)} accepted Yosys/ABC depth pass(es)"
        ),
        "changed": legalization_changes + (best_result.get("changed", []) if isinstance(best_result, dict) else []),
        "num_changed_outputs": best_result.get("num_changed_outputs", 0) if isinstance(best_result, dict) else 0,
    }


def _normalize_cone_gate_constraints(constraints: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for constraint in constraints or []:
        if not isinstance(constraint, dict):
            raise ValueError("Optimization constraints must be objects.")
        if constraint.get("type") != "cone_gate_library":
            raise ValueError("Only cone_gate_library constraints are supported.")
        target = constraint.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("cone_gate_library constraint requires a non-empty target.")
        allowed = _normalize_allowed_gates(constraint.get("allowed_gates"))
        if not allowed:
            raise ValueError("cone_gate_library constraint requires allowed_gates.")
        normalized.append({"type": "cone_gate_library", "target": target.strip(), "allowed_gates": sorted(allowed)})
    return normalized


def _optimize_design_depth_with_constraints(
    design: Design,
    *,
    max_depth: int | None,
    max_outputs: int | None,
    cost_function: str,
    cost_scope: str,
    constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)
    before_constraints = [_cone_gate_constraint_report(design, constraint) for constraint in constraints]

    legalization_changes: list[dict[str, Any]] = []
    for constraint in constraints:
        resolved = _resolve_optimization_cone_target(design, constraint["target"])
        legalize = _legalize_cone_to_gate_library(design, resolved["target"], set(constraint["allowed_gates"]))
        if legalize["num_changed"]:
            legalization_changes.append({"target": constraint["target"], "resolved_target": resolved["target"], **legalize})
        balance = _optimize_constrained_cone_depth_locally(design, constraint)
        if balance["num_changed"]:
            legalization_changes.append({"rule": "constraint_cone_depth_balance", **balance})
        region = _try_multi_output_region_yosys_for_constraint(design, constraint)
        if region["num_changed"]:
            legalization_changes.append({"rule": "constraint_multi_output_region_yosys", **region})
    whole_regions = _optimize_whole_design_multi_output_regions(design, constraints, max_regions=max_outputs)
    legalization_changes.extend(whole_regions)
    rebuild_graph(design)

    legalized_design = deepcopy(design)
    legalized_gate_count = len(design.gates)
    legalized_depth = _design_max_depth(design)
    legalized_constraints = [_cone_gate_constraint_report(design, constraint) for constraint in constraints]
    if not all(report["satisfied"] for report in legalized_constraints):
        bad = [report for report in legalized_constraints if not report["satisfied"]]
        raise RuntimeError(f"Constrained depth optimization failed to legalize cone constraints: {bad}")

    candidate_reject_reason = "No depth candidate was attempted."
    candidate_result: dict[str, Any] | None = None
    candidate_applied = False
    final_constraints = legalized_constraints
    try:
        candidate = deepcopy(legalized_design)
        candidate_result = _optimize_design_depth_base(candidate, max_depth=max_depth, max_outputs=max_outputs)
        candidate_relegalization: list[dict[str, Any]] = []
        candidate_constraints = [_cone_gate_constraint_report(candidate, constraint) for constraint in constraints]
        if not all(report["satisfied"] for report in candidate_constraints):
            for constraint in constraints:
                resolved = _resolve_optimization_cone_target(candidate, constraint["target"])
                relegalize = _legalize_cone_to_gate_library(candidate, resolved["target"], set(constraint["allowed_gates"]))
                if relegalize["num_changed"]:
                    candidate_relegalization.append({"target": constraint["target"], "resolved_target": resolved["target"], **relegalize})
            candidate_constraints = [_cone_gate_constraint_report(candidate, constraint) for constraint in constraints]
        candidate_depth = _design_max_depth(candidate)
        candidate_gate_count = len(candidate.gates)
        if not all(report["satisfied"] for report in candidate_constraints):
            candidate_reject_reason = "depth candidate violated cone gate-library constraint after re-legalization"
        elif candidate_depth > legalized_depth:
            candidate_reject_reason = f"depth candidate increased post-legalization depth from {legalized_depth} to {candidate_depth}"
        elif candidate_depth == legalized_depth and candidate_gate_count > legalized_gate_count:
            candidate_reject_reason = f"depth candidate kept depth {candidate_depth} but increased gate count from {legalized_gate_count} to {candidate_gate_count}"
        else:
            if candidate_relegalization:
                candidate_result.setdefault("changed", []).extend(candidate_relegalization)
            _replace_design_contents(design, candidate)
            candidate_applied = True
            final_constraints = candidate_constraints
    except Exception as exc:
        candidate_reject_reason = str(exc)
        _replace_design_contents(design, legalized_design)

    if not candidate_applied:
        _replace_design_contents(design, legalized_design)

    final_depth = _design_max_depth(design)
    final_gate_count = len(design.gates)
    target_met = max_depth is None or final_depth <= max_depth
    return {
        "engine": "constraint_aware_depth",
        "cost_function": cost_function,
        "cost_scope": cost_scope,
        "max_depth": max_depth,
        "max_outputs": max_outputs,
        "attempted_outputs": candidate_result.get("attempted_outputs", []) if isinstance(candidate_result, dict) else [],
        "skipped_outputs": candidate_result.get("skipped_outputs", []) if isinstance(candidate_result, dict) else [],
        "initial_gate_count": initial_gate_count,
        "final_gate_count": final_gate_count,
        "initial_depth": initial_depth,
        "legalized_gate_count": legalized_gate_count,
        "legalized_depth": legalized_depth,
        "final_depth": final_depth,
        "target_met": target_met,
        "constraints": constraints,
        "constraint_reports_before": before_constraints,
        "constraint_reports_after": final_constraints,
        "legalization_changes": legalization_changes,
        "candidate_engine": candidate_result.get("engine") if isinstance(candidate_result, dict) else None,
        "candidate_applied": candidate_applied,
        "fallback_reason": None if candidate_applied else candidate_reject_reason,
        "changed": legalization_changes + (candidate_result.get("changed", []) if candidate_applied and isinstance(candidate_result, dict) else []),
        "num_changed_outputs": candidate_result.get("num_changed_outputs", 0) if candidate_applied and isinstance(candidate_result, dict) else 0,
        "bounded_reason": candidate_result.get("bounded_reason") if isinstance(candidate_result, dict) else None,
    }


def _cone_gate_constraint_report(design: Design, constraint: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve_optimization_cone_target(design, constraint["target"])
    resolved_target = resolved["target"]
    allowed = set(constraint["allowed_gates"])
    disallowed = sorted(set(_disallowed_gate_types_in_cone(design, resolved_target, allowed)))
    return {
        "type": "cone_gate_library",
        "target": constraint["target"],
        "resolved_target": resolved_target,
        "resolution_kind": resolved["kind"],
        "allowed_gates": sorted(allowed),
        "satisfied": not disallowed,
        "disallowed_gate_types": disallowed,
        "gate_count": len(logic_cone(design, resolved_target)),
        "depth": _cone_max_depth(design, resolved_target),
    }


def _optimize_whole_design_multi_output_regions(
    design: Design,
    constraints: list[dict[str, Any]],
    *,
    max_regions: int | None = None,
) -> list[dict[str, Any]]:
    gate_count = len(design.gates)
    has_fanout_buffers = any("__fanout_buf_" in net for net in design.all_nets())
    limit = max_regions if max_regions is not None else min(_adaptive_depth_topk(gate_count, has_fanout_buffers), 8)
    limit = max(0, min(limit, 8))
    if limit == 0:
        return []

    changed: list[dict[str, Any]] = []
    tried: set[str] = set()
    for target in _whole_design_critical_targets(design, limit=limit * 2):
        if target in tried:
            continue
        tried.add(target)
        result = _try_multi_output_region_yosys_for_target(
            design,
            target,
            constraints=constraints,
        )
        if result["num_changed"]:
            changed.append({"rule": "whole_design_multi_output_region_yosys", **result})
        if len(changed) >= limit:
            break
    return changed


def _whole_design_critical_targets(design: Design, *, limit: int) -> list[str]:
    profile = _fast_depth_profile(design)
    scored = [
        (depth, target)
        for target, depth in profile["endpoint_depths"].items()
        if depth > 0 and target in design.all_nets()
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [target for _, target in scored[:limit]]


def _try_multi_output_region_yosys_for_target(
    design: Design,
    target: str,
    *,
    constraints: list[dict[str, Any]],
    max_region_gates: int = 3000,
    max_region_outputs: int = 128,
) -> dict[str, Any]:
    resolved = _resolve_optimization_cone_target(design, target)
    resolved_target = resolved["target"]
    region_gate_names = set(logic_cone(design, resolved_target))
    initial_depth = _cone_max_depth(design, resolved_target)
    initial_design_depth = _design_max_depth(design)
    initial_total_gates = len(design.gates)
    initial_gate_count = len(region_gate_names)
    if initial_gate_count < 8:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "region is too small")
    if initial_gate_count > max_region_gates:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, f"region has {initial_gate_count} gates, above limit {max_region_gates}")

    region_outputs = _multi_output_region_outputs(design, region_gate_names, resolved_target)
    if len(region_outputs) > max_region_outputs:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, f"region has {len(region_outputs)} outputs, above limit {max_region_outputs}")
    before_output_depths = {net: _cone_max_depth(design, net) for net in region_outputs}
    region_design, boundary_inputs = _build_multi_output_region_design(design, region_gate_names, region_outputs)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            raw_input = tmp_dir / "whole_region_input.v"
            yosys_input = tmp_dir / "whole_region_yosys_input.v"
            optimized_output = tmp_dir / "whole_region_optimized.v"
            write_verilog(region_design, raw_input)
            _write_yosys_abc_input(raw_input, yosys_input)
            script = _build_yosys_abc_cone_script(
                input_path=yosys_input,
                output_path=optimized_output,
                top_module=region_design.module_name,
                max_depth=None,
            )
            optimized_region, reason = _run_yosys_depth_attempt(
                script,
                tmp_dir=tmp_dir,
                output_path=optimized_output,
                timeout=_cone_yosys_abc_timeout(initial_gate_count),
                label="whole_design_multi_output_region_yosys_abc",
            )
            if optimized_region is None:
                return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, reason)
    except Exception as exc:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, str(exc))

    _restore_boundary_bit_aliases(optimized_region, boundary_inputs + region_outputs)
    missing_outputs = sorted(set(region_outputs) - optimized_region.outputs)
    if missing_outputs:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "optimized region missing outputs: " + ";".join(missing_outputs[:8]))

    candidate = deepcopy(design)
    _splice_multi_output_region(
        candidate,
        old_region_gate_names=region_gate_names,
        optimized_region=optimized_region,
        boundary_inputs=boundary_inputs,
        region_outputs=region_outputs,
    )
    relegalized = _relegalize_constraints(candidate, constraints)
    reports = [_cone_gate_constraint_report(candidate, constraint) for constraint in constraints]
    if not all(report["satisfied"] for report in reports):
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "candidate violated cone gate-library constraint")
    if not check_connectivity(candidate).get("ok"):
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "candidate failed connectivity check")

    after_output_depths = {net: _cone_max_depth(candidate, net) for net in region_outputs if net in candidate.all_nets()}
    depth_regressions = {
        net: (before_output_depths[net], after_output_depths[net])
        for net in after_output_depths
        if after_output_depths[net] > before_output_depths[net]
    }
    final_depth = _cone_max_depth(candidate, resolved_target)
    final_design_depth = _design_max_depth(candidate)
    final_total_gates = len(candidate.gates)
    if depth_regressions:
        sample = ";".join(f"{net}:{old}->{new}" for net, (old, new) in list(depth_regressions.items())[:6])
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "region output depth regression: " + sample)
    if final_design_depth > initial_design_depth:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, f"whole-design depth regression {initial_design_depth}->{final_design_depth}")

    improves_target = final_depth < initial_depth
    improves_design = final_design_depth < initial_design_depth
    improves_area = final_design_depth == initial_design_depth and final_total_gates < initial_total_gates
    gate_budget = initial_total_gates + max(500, initial_gate_count)
    if not (improves_target or improves_design or improves_area):
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, "candidate did not improve target depth, design depth, or gate count")
    if final_total_gates > gate_budget:
        return _target_region_noop(target, resolved_target, initial_gate_count, initial_depth, f"candidate exceeded gate budget {final_total_gates}>{gate_budget}")

    _replace_design_contents(design, candidate)
    return {
        "target": target,
        "resolved_target": resolved_target,
        "engine": "whole_design_multi_output_region_yosys_abc",
        "initial_gate_count": initial_gate_count,
        "final_gate_count": len(logic_cone(design, resolved_target)),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "initial_design_depth": initial_design_depth,
        "final_design_depth": final_design_depth,
        "initial_total_gates": initial_total_gates,
        "final_total_gates": final_total_gates,
        "region_output_count": len(region_outputs),
        "boundary_input_count": len(boundary_inputs),
        "optimized_region_gate_count": len(optimized_region.gates),
        "relegalized_gate_count": relegalized,
        "changed": [{"rule": "whole_design_multi_output_region_yosys_abc", "region_outputs": region_outputs[:16], "boundary_inputs": boundary_inputs[:16]}],
        "num_changed": 1,
    }


def _relegalize_constraints(design: Design, constraints: list[dict[str, Any]]) -> int:
    total = 0
    for constraint in constraints:
        resolved = _resolve_optimization_cone_target(design, constraint["target"])
        result = _legalize_cone_to_gate_library(design, resolved["target"], set(constraint["allowed_gates"]))
        total += result["num_changed"]
    return total


def _target_region_noop(target: str, resolved_target: str, gate_count: int, depth: int, reason: str) -> dict[str, Any]:
    return {
        "target": target,
        "resolved_target": resolved_target,
        "engine": "whole_design_multi_output_region_yosys_abc",
        "initial_gate_count": gate_count,
        "final_gate_count": gate_count,
        "initial_depth": depth,
        "final_depth": depth,
        "changed": [],
        "num_changed": 0,
        "reason": reason,
    }

def _try_multi_output_region_yosys_for_constraint(
    design: Design,
    constraint: dict[str, Any],
    *,
    max_region_gates: int = 3000,
    max_region_outputs: int = 128,
) -> dict[str, Any]:
    resolved = _resolve_optimization_cone_target(design, constraint["target"])
    resolved_target = resolved["target"]
    allowed = set(constraint["allowed_gates"])
    region_gate_names = set(logic_cone(design, resolved_target))
    initial_depth = _cone_max_depth(design, resolved_target)
    initial_gate_count = len(region_gate_names)
    if initial_gate_count < 8:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "region is too small")
    if initial_gate_count > max_region_gates:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, f"region has {initial_gate_count} gates, above limit {max_region_gates}")

    region_outputs = _multi_output_region_outputs(design, region_gate_names, resolved_target)
    if len(region_outputs) > max_region_outputs:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, f"region has {len(region_outputs)} outputs, above limit {max_region_outputs}")
    before_output_depths = {net: _cone_max_depth(design, net) for net in region_outputs}
    region_design, boundary_inputs = _build_multi_output_region_design(design, region_gate_names, region_outputs)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            raw_input = tmp_dir / "multi_region_input.v"
            yosys_input = tmp_dir / "multi_region_yosys_input.v"
            optimized_output = tmp_dir / "multi_region_optimized.v"
            write_verilog(region_design, raw_input)
            _write_yosys_abc_input(raw_input, yosys_input)
            script = _build_yosys_abc_cone_script(
                input_path=yosys_input,
                output_path=optimized_output,
                top_module=region_design.module_name,
                max_depth=None,
            )
            optimized_region, reason = _run_yosys_depth_attempt(
                script,
                tmp_dir=tmp_dir,
                output_path=optimized_output,
                timeout=_cone_yosys_abc_timeout(initial_gate_count),
                label="multi_output_region_yosys_abc",
            )
            if optimized_region is None:
                return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, reason)
    except Exception as exc:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, str(exc))

    _restore_boundary_bit_aliases(optimized_region, boundary_inputs + region_outputs)
    missing_outputs = sorted(set(region_outputs) - optimized_region.outputs)
    if missing_outputs:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "optimized region missing outputs: " + ";".join(missing_outputs[:8]))

    candidate = deepcopy(design)
    _splice_multi_output_region(
        candidate,
        old_region_gate_names=region_gate_names,
        optimized_region=optimized_region,
        boundary_inputs=boundary_inputs,
        region_outputs=region_outputs,
    )
    relegalize = _legalize_cone_to_gate_library(candidate, resolved_target, allowed)
    report = _cone_gate_constraint_report(candidate, constraint)
    if not report["satisfied"]:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "candidate violated cone gate-library constraint")
    connectivity = check_connectivity(candidate)
    if not connectivity.get("ok"):
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "candidate failed connectivity check")

    after_output_depths = {net: _cone_max_depth(candidate, net) for net in region_outputs if net in candidate.all_nets()}
    depth_regressions = {
        net: (before_output_depths[net], after_output_depths[net])
        for net in after_output_depths
        if after_output_depths[net] > before_output_depths[net]
    }
    final_depth = _cone_max_depth(candidate, resolved_target)
    final_gate_count = len(logic_cone(candidate, resolved_target))
    if depth_regressions:
        sample = ";".join(f"{net}:{old}->{new}" for net, (old, new) in list(depth_regressions.items())[:6])
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "region output depth regression: " + sample)
    if final_depth >= initial_depth and final_gate_count >= initial_gate_count:
        return _multi_region_noop(constraint, resolved_target, initial_gate_count, initial_depth, "candidate did not improve constrained cone depth or gate count")

    _replace_design_contents(design, candidate)
    return {
        "target": constraint["target"],
        "resolved_target": resolved_target,
        "allowed_gates": sorted(allowed),
        "engine": "multi_output_region_yosys_abc",
        "initial_gate_count": initial_gate_count,
        "final_gate_count": final_gate_count,
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "region_output_count": len(region_outputs),
        "boundary_input_count": len(boundary_inputs),
        "optimized_region_gate_count": len(optimized_region.gates),
        "relegalized_gate_count": relegalize["num_changed"],
        "changed": [
            {
                "rule": "multi_output_region_yosys_abc",
                "region_outputs": region_outputs[:16],
                "boundary_inputs": boundary_inputs[:16],
            }
        ],
        "num_changed": 1,
    }


def _multi_region_noop(
    constraint: dict[str, Any],
    resolved_target: str,
    gate_count: int,
    depth: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "target": constraint["target"],
        "resolved_target": resolved_target,
        "allowed_gates": sorted(set(constraint["allowed_gates"])),
        "engine": "multi_output_region_yosys_abc",
        "initial_gate_count": gate_count,
        "final_gate_count": gate_count,
        "initial_depth": depth,
        "final_depth": depth,
        "changed": [],
        "num_changed": 0,
        "reason": reason,
    }


def _multi_output_region_outputs(design: Design, region_gate_names: set[str], resolved_target: str) -> list[str]:
    rebuild_graph(design)
    outputs: set[str] = {resolved_target}
    for gate_name in region_gate_names:
        gate = design.gates[gate_name]
        for sink in design.fanouts.get(gate.output, []):
            if sink.startswith("GATE:") and sink.split(":", 1)[1] in region_gate_names:
                continue
            outputs.add(gate.output)
            break
    return sorted(outputs)


def _build_multi_output_region_design(
    design: Design,
    region_gate_names: set[str],
    region_outputs: list[str],
) -> tuple[Design, list[str]]:
    boundary_inputs: list[str] = []
    boundary_seen: set[str] = set()
    for gate_name in sorted(region_gate_names):
        gate = design.gates[gate_name]
        for net in gate.inputs:
            if is_constant(net):
                continue
            driver = design.drivers.get(net)
            if driver and driver.startswith("GATE:") and driver.split(":", 1)[1] in region_gate_names:
                continue
            if net not in boundary_seen:
                boundary_seen.add(net)
                boundary_inputs.append(net)

    region_design = Design(
        module_name="multi_region_opt",
        inputs=set(boundary_inputs),
        outputs=set(region_outputs),
    )
    for gate_name in sorted(region_gate_names):
        gate = design.gates[gate_name]
        region_design.add_gate(
            Gate(
                name=gate.name,
                type=gate.type,
                inputs=list(gate.inputs),
                output=gate.output,
                attrs=dict(gate.attrs),
            )
        )
    rebuild_graph(region_design)
    return region_design, boundary_inputs


def _splice_multi_output_region(
    design: Design,
    *,
    old_region_gate_names: set[str],
    optimized_region: Design,
    boundary_inputs: list[str],
    region_outputs: list[str],
) -> None:
    for gate_name in old_region_gate_names:
        design.gates.pop(gate_name, None)

    net_map: dict[str, str] = {net: net for net in boundary_inputs}
    net_map.update({net: net for net in region_outputs})
    for constant in ("1'b0", "1'b1", "1'bx", "1'bz", "0", "1"):
        net_map[constant] = constant

    for gate_name in sorted(optimized_region.gates):
        gate = optimized_region.gates[gate_name]
        mapped_inputs = [_map_region_net(design, net_map, net) for net in gate.inputs]
        mapped_output = _map_region_net(design, net_map, gate.output)
        new_gate_name = design.make_unique_gate_name(f"multi_region_abc_{gate.name}")
        design.add_gate(
            Gate(
                name=new_gate_name,
                type=gate.type,
                inputs=mapped_inputs,
                output=mapped_output,
                attrs=dict(gate.attrs),
            )
        )
    rebuild_graph(design)
    _prune_unreferenced_region_wires(design)


def _map_region_net(design: Design, net_map: dict[str, str], net: str) -> str:
    if net in net_map:
        return net_map[net]
    if is_constant(net):
        net_map[net] = net
        return net
    mapped = design.make_unique_wire_name(f"multi_region_{net}")
    net_map[net] = mapped
    return mapped


def _prune_unreferenced_region_wires(design: Design) -> None:
    referenced = set(design.inputs) | set(design.outputs)
    for gate in design.gates.values():
        referenced.add(gate.output)
        referenced.update(net for net in gate.inputs if not is_constant(net))
    for dff in design.dffs.values():
        referenced.add(dff.q)
        if not is_constant(dff.d):
            referenced.add(dff.d)
        if dff.clk:
            referenced.add(dff.clk)
        if dff.rst:
            referenced.add(dff.rst)
    design.wires = {net for net in design.wires if net in referenced}
    rebuild_graph(design)


def _restore_boundary_bit_aliases(optimized_region: Design, boundary_nets: list[str]) -> None:
    by_base: dict[str, list[str]] = {}
    for net in boundary_nets:
        if "[" not in net or not net.endswith("]"):
            continue
        base = net.split("[", 1)[0]
        by_base.setdefault(base, []).append(net)
    aliases = {base: nets[0] for base, nets in by_base.items() if len(nets) == 1}
    active_aliases = {base: original for base, original in aliases.items() if base in optimized_region.inputs or base in optimized_region.outputs}
    if not active_aliases:
        return
    optimized_region.inputs = {active_aliases.get(net, net) for net in optimized_region.inputs}
    optimized_region.outputs = {active_aliases.get(net, net) for net in optimized_region.outputs}
    optimized_region.wires = {active_aliases.get(net, net) for net in optimized_region.wires}
    for gate in optimized_region.gates.values():
        gate.inputs = [active_aliases.get(net, net) for net in gate.inputs]
        gate.output = active_aliases.get(gate.output, gate.output)
    rebuild_graph(optimized_region)

def _optimize_constrained_cone_depth_locally(design: Design, constraint: dict[str, Any]) -> dict[str, Any]:
    allowed = set(constraint["allowed_gates"])
    if allowed != {"nor", "not"}:
        return {"target": constraint["target"], "allowed_gates": sorted(allowed), "changed": [], "num_changed": 0}

    resolved = _resolve_optimization_cone_target(design, constraint["target"])
    resolved_target = resolved["target"]
    initial_depth = _cone_max_depth(design, resolved_target)
    initial_gate_count = len(logic_cone(design, resolved_target))
    working = deepcopy(design)
    changed: list[dict[str, Any]] = []

    for _ in range(12):
        rebuild_graph(working)
        current_depth = _cone_max_depth(working, resolved_target)
        cone_gate_names = set(logic_cone(working, resolved_target))
        candidate_nets = {resolved_target}
        candidate_nets.update(
            gate.output
            for name, gate in working.gates.items()
            if name in cone_gate_names and gate.type in {"not", "nor"}
        )
        accepted = None
        for net in sorted(candidate_nets, key=lambda item: _cone_max_depth(working, item), reverse=True):
            for virtual_op in ("or", "and"):
                trial = deepcopy(working)
                rewrite = _try_balance_virtual_nor_not_tree(trial, net, virtual_op)
                if rewrite is None:
                    continue
                if not check_connectivity(trial).get("ok"):
                    continue
                report = _cone_gate_constraint_report(trial, constraint)
                if not report["satisfied"]:
                    continue
                trial_depth = _cone_max_depth(trial, resolved_target)
                if trial_depth < current_depth:
                    accepted = (trial, rewrite, trial_depth)
                    break
            if accepted is not None:
                break
        if accepted is None:
            break
        working, rewrite, _ = accepted
        changed.append(rewrite)

    final_depth = _cone_max_depth(working, resolved_target)
    if changed and final_depth < initial_depth:
        _replace_design_contents(design, working)
        final_gate_count = len(logic_cone(design, resolved_target))
        return {
            "target": constraint["target"],
            "resolved_target": resolved_target,
            "allowed_gates": sorted(allowed),
            "initial_depth": initial_depth,
            "final_depth": final_depth,
            "initial_gate_count": initial_gate_count,
            "final_gate_count": final_gate_count,
            "changed": changed,
            "num_changed": len(changed),
        }
    return {
        "target": constraint["target"],
        "resolved_target": resolved_target,
        "allowed_gates": sorted(allowed),
        "initial_depth": initial_depth,
        "final_depth": initial_depth,
        "initial_gate_count": initial_gate_count,
        "final_gate_count": initial_gate_count,
        "changed": [],
        "num_changed": 0,
    }


def _try_balance_virtual_nor_not_tree(design: Design, output_net: str, virtual_op: str) -> dict[str, Any] | None:
    collected = _collect_virtual_nor_not_tree(design, output_net, virtual_op)
    if collected is None:
        return None
    leaves, removable_gates = collected
    if len(leaves) < 3 or len(removable_gates) < 3:
        return None
    old_depth = _cone_max_depth(design, output_net)
    for gate_name in removable_gates:
        design.gates.pop(gate_name, None)
    builder = _GateLibraryEmitter(design, f"balance_{virtual_op}_{output_net}")
    _emit_balanced_nor_not_op(builder, virtual_op, leaves, output_net)
    rebuild_graph(design)
    new_depth = _cone_max_depth(design, output_net)
    if new_depth >= old_depth:
        return None
    return {
        "rule": f"balance_virtual_{virtual_op}_tree",
        "output": output_net,
        "leaf_count": len(leaves),
        "removed_gates": sorted(removable_gates),
        "old_depth": old_depth,
        "new_depth": new_depth,
    }


def _collect_virtual_nor_not_tree(design: Design, output_net: str, virtual_op: str) -> tuple[list[str], set[str]] | None:
    if virtual_op == "or":
        return _collect_virtual_or_tree(design, output_net)
    if virtual_op == "and":
        return _collect_virtual_and_tree(design, output_net)
    return None


def _collect_virtual_or_tree(design: Design, output_net: str) -> tuple[list[str], set[str]] | None:
    not_gate = _gate_driving_net(design, output_net)
    if not_gate is None or not_gate.type != "not" or len(not_gate.inputs) != 1:
        return None
    mid_net = not_gate.inputs[0]
    nor_gate = _gate_driving_net(design, mid_net)
    if nor_gate is None or nor_gate.type != "nor" or len(nor_gate.inputs) < 2:
        return None
    if design.fanouts.get(mid_net, []) != [f"GATE:{not_gate.name}"]:
        return None

    leaves: list[str] = []
    removable = {not_gate.name, nor_gate.name}
    for input_net in nor_gate.inputs:
        if _has_single_gate_fanout(design, input_net, nor_gate.name):
            nested = _collect_virtual_or_tree(design, input_net)
        else:
            nested = None
        if nested is None:
            leaves.append(input_net)
        else:
            nested_leaves, nested_gates = nested
            leaves.extend(nested_leaves)
            removable.update(nested_gates)
    return leaves, removable


def _collect_virtual_and_tree(design: Design, output_net: str) -> tuple[list[str], set[str]] | None:
    nor_gate = _gate_driving_net(design, output_net)
    if nor_gate is None or nor_gate.type != "nor" or len(nor_gate.inputs) < 2:
        return None

    leaves: list[str] = []
    removable = {nor_gate.name}
    for inverted_net in nor_gate.inputs:
        not_gate = _gate_driving_net(design, inverted_net)
        if not_gate is None or not_gate.type != "not" or len(not_gate.inputs) != 1:
            return None
        if design.fanouts.get(inverted_net, []) != [f"GATE:{nor_gate.name}"]:
            return None
        source_net = not_gate.inputs[0]
        removable.add(not_gate.name)
        if _has_single_gate_fanout(design, source_net, not_gate.name):
            nested = _collect_virtual_and_tree(design, source_net)
        else:
            nested = None
        if nested is None:
            leaves.append(source_net)
        else:
            nested_leaves, nested_gates = nested
            leaves.extend(nested_leaves)
            removable.update(nested_gates)
    return leaves, removable


def _gate_driving_net(design: Design, net: str) -> Gate | None:
    driver = design.drivers.get(net)
    if not driver or not driver.startswith("GATE:"):
        return None
    return design.gates.get(driver.split(":", 1)[1])


def _has_single_gate_fanout(design: Design, net: str, gate_name: str) -> bool:
    return design.fanouts.get(net, []) == [f"GATE:{gate_name}"]


def _emit_balanced_nor_not_op(builder: _GateLibraryEmitter, virtual_op: str, leaves: list[str], output: str) -> str:
    if len(leaves) == 1:
        first_not = builder.gate("not", [leaves[0]])
        return builder.gate("not", [first_not], output)
    midpoint = len(leaves) // 2
    left = _emit_balanced_nor_not_op(builder, virtual_op, leaves[:midpoint], None)
    right = _emit_balanced_nor_not_op(builder, virtual_op, leaves[midpoint:], None)
    if virtual_op == "or":
        nor_net = builder.gate("nor", [left, right])
        return builder.gate("not", [nor_net], output)
    left_not = builder.gate("not", [left])
    right_not = builder.gate("not", [right])
    return builder.gate("nor", [left_not, right_not], output)

def _normalize_allowed_gates(allowed_gates: list[str] | set[str] | tuple[str, ...] | None) -> set[str]:
    if not allowed_gates:
        return set()
    return {str(gate).strip().lower() for gate in allowed_gates if str(gate).strip()}


def _cone_uses_only_gates(design: Design, resolved_target: str, allowed: set[str]) -> bool:
    return not _disallowed_gate_types_in_cone(design, resolved_target, allowed)


def _disallowed_gate_types_in_cone(design: Design, resolved_target: str, allowed: set[str]) -> list[str]:
    rebuild_graph(design)
    bad = []
    for gate_name in logic_cone(design, resolved_target):
        gate = design.gates.get(gate_name)
        if gate is not None and gate.type not in allowed:
            bad.append(gate.type)
    return bad


def _design_gate_library_report(design: Design, allowed: set[str]) -> dict[str, Any]:
    disallowed: dict[str, int] = {}
    for gate in design.gates.values():
        if gate.type not in allowed:
            disallowed[gate.type] = disallowed.get(gate.type, 0) + 1
    return {
        "allowed_gates": sorted(allowed),
        "satisfied": not disallowed,
        "gate_count": len(design.gates),
        "disallowed_gate_counts": dict(sorted(disallowed.items())),
        "depth": _design_max_depth(design),
    }


def _legalize_design_to_gate_library(design: Design, allowed: set[str]) -> dict[str, Any]:
    if allowed not in ({"and", "or", "not"}, {"and", "not"}, {"nand", "not"}, {"nor", "not"}):
        raise ValueError(f"Unsupported allowed_gates for whole-design optimization: {sorted(allowed)}")
    changed: list[dict[str, Any]] = []
    names = _CachedNameAllocator(design)
    for gate_name in sorted(list(design.gates)):
        gate = design.gates.get(gate_name)
        if gate is None or gate.type in allowed:
            continue
        old_type = gate.type
        old_output = gate.output
        old_inputs = list(gate.inputs)
        design.gates.pop(gate_name, None)
        builder = _GateLibraryEmitter(design, gate_name, names)
        if allowed == {"and", "or", "not"}:
            _emit_gate_as_and_or_not(builder, old_type, old_inputs, old_output)
        elif allowed == {"and", "not"}:
            _emit_gate_as_and_not(builder, old_type, old_inputs, old_output)
        elif allowed == {"nand", "not"}:
            _emit_gate_as_nand_not(builder, old_type, old_inputs, old_output)
        else:
            _emit_gate_as_nor_not(builder, old_type, old_inputs, old_output)
        changed.append({"gate": gate_name, "old_type": old_type, "output": old_output, "allowed_gates": sorted(allowed)})
    rebuild_graph(design)
    return {"changed": changed, "num_changed": len(changed)}


def _legalize_cone_to_gate_library(design: Design, resolved_target: str, allowed: set[str]) -> dict[str, Any]:
    if allowed not in ({"and", "or", "not"}, {"and", "not"}, {"nand", "not"}, {"nor", "not"}):
        raise ValueError(f"Unsupported allowed_gates for constrained cone optimization: {sorted(allowed)}")
    rebuild_graph(design)
    changed: list[dict[str, Any]] = []
    names = _CachedNameAllocator(design)
    for gate_name in sorted(list(logic_cone(design, resolved_target))):
        gate = design.gates.get(gate_name)
        if gate is None or gate.type in allowed:
            continue
        old_type = gate.type
        old_output = gate.output
        old_inputs = list(gate.inputs)
        design.gates.pop(gate_name, None)
        builder = _GateLibraryEmitter(design, gate_name, names)
        if allowed == {"and", "or", "not"}:
            _emit_gate_as_and_or_not(builder, old_type, old_inputs, old_output)
        elif allowed == {"and", "not"}:
            _emit_gate_as_and_not(builder, old_type, old_inputs, old_output)
        elif allowed == {"nand", "not"}:
            _emit_gate_as_nand_not(builder, old_type, old_inputs, old_output)
        else:
            _emit_gate_as_nor_not(builder, old_type, old_inputs, old_output)
        changed.append({"gate": gate_name, "old_type": old_type, "output": old_output, "allowed_gates": sorted(allowed)})
    rebuild_graph(design)
    return {"changed": changed, "num_changed": len(changed)}


class _GateLibraryEmitter:
    def __init__(self, design: Design, base_name: str, names: _CachedNameAllocator | None = None) -> None:
        self.design = design
        self.base_name = base_name
        self.names = names or _CachedNameAllocator(design)
        self.index = 0

    def wire(self, base: str) -> str:
        return self.names.wire(base)

    def gate(self, gate_type: str, inputs: list[str], output: str | None = None) -> str:
        self.index += 1
        out = output or self.names.wire(f"{self.base_name}_{gate_type}_w_{self.index}")
        name = self.names.gate_name(f"{self.base_name}_{gate_type}_{self.index}")
        self.design.add_gate(Gate(name=name, type=gate_type, inputs=inputs, output=out))
        return out


def _emit_gate_as_and_or_not(builder: _GateLibraryEmitter, gate_type: str, inputs: list[str], output: str) -> None:
    if gate_type == "buf" and len(inputs) == 1:
        mid = builder.gate("not", [inputs[0]])
        builder.gate("not", [mid], output)
    elif gate_type == "and":
        builder.gate("and", inputs, output)
    elif gate_type == "or":
        builder.gate("or", inputs, output)
    elif gate_type == "not" and len(inputs) == 1:
        builder.gate("not", [inputs[0]], output)
    elif gate_type == "nand":
        mid = builder.gate("and", inputs)
        builder.gate("not", [mid], output)
    elif gate_type == "nor":
        mid = builder.gate("or", inputs)
        builder.gate("not", [mid], output)
    elif gate_type == "xor" and len(inputs) == 2:
        _emit_xor_as_and_or_not(builder, inputs[0], inputs[1], output)
    elif gate_type == "xnor" and len(inputs) == 2:
        mid = builder.wire(f"{builder.base_name}_xnor_xor")
        _emit_xor_as_and_or_not(builder, inputs[0], inputs[1], mid)
        builder.gate("not", [mid], output)
    else:
        raise ValueError(f"Cannot legalize {gate_type} with {len(inputs)} input(s) to AND/OR/NOT")


def _emit_gate_as_and_not(builder: _GateLibraryEmitter, gate_type: str, inputs: list[str], output: str) -> None:
    if gate_type == "or":
        inverted = [builder.gate("not", [net]) for net in inputs]
        mid = builder.gate("and", inverted)
        builder.gate("not", [mid], output)
    elif gate_type == "nor":
        inverted = [builder.gate("not", [net]) for net in inputs]
        builder.gate("and", inverted, output)
    elif gate_type == "xor" and len(inputs) == 2:
        _emit_xor_as_and_not(builder, inputs[0], inputs[1], output)
    elif gate_type == "xnor" and len(inputs) == 2:
        mid = builder.wire(f"{builder.base_name}_xnor_xor")
        _emit_xor_as_and_not(builder, inputs[0], inputs[1], mid)
        builder.gate("not", [mid], output)
    else:
        _emit_gate_as_and_or_not(builder, gate_type, inputs, output)


def _emit_gate_as_nand_not(builder: _GateLibraryEmitter, gate_type: str, inputs: list[str], output: str) -> None:
    def inv(net: str, out: str | None = None) -> str:
        return builder.gate("not", [net], out)
    def and_gate(nets: list[str], out: str | None = None) -> str:
        return inv(builder.gate("nand", nets), out)
    def or_gate(nets: list[str], out: str | None = None) -> str:
        return builder.gate("nand", [inv(net) for net in nets], out)
    if gate_type == "buf" and len(inputs) == 1:
        inv(inv(inputs[0]), output)
    elif gate_type == "not" and len(inputs) == 1:
        inv(inputs[0], output)
    elif gate_type == "nand":
        builder.gate("nand", inputs, output)
    elif gate_type == "and":
        and_gate(inputs, output)
    elif gate_type == "or":
        or_gate(inputs, output)
    elif gate_type == "nor":
        inv(or_gate(inputs), output)
    elif gate_type == "xor" and len(inputs) == 2:
        a, b = inputs
        t1 = builder.gate("nand", [a, b])
        t2 = builder.gate("nand", [a, t1])
        t3 = builder.gate("nand", [b, t1])
        builder.gate("nand", [t2, t3], output)
    elif gate_type == "xnor" and len(inputs) == 2:
        a, b = inputs
        t1 = builder.gate("nand", [a, b])
        t2 = builder.gate("nand", [a, t1])
        t3 = builder.gate("nand", [b, t1])
        xor_net = builder.gate("nand", [t2, t3])
        inv(xor_net, output)
    else:
        raise ValueError(f"Cannot legalize {gate_type} with {len(inputs)} input(s) to NAND/NOT")


def _emit_gate_as_nor_not(builder: _GateLibraryEmitter, gate_type: str, inputs: list[str], output: str) -> None:
    def inv(net: str, out: str | None = None) -> str:
        return builder.gate("not", [net], out)
    def or_gate(nets: list[str], out: str | None = None) -> str:
        return inv(builder.gate("nor", nets), out)
    def and_gate(nets: list[str], out: str | None = None) -> str:
        return builder.gate("nor", [inv(net) for net in nets], out)
    if gate_type == "buf" and len(inputs) == 1:
        inv(inv(inputs[0]), output)
    elif gate_type == "not" and len(inputs) == 1:
        inv(inputs[0], output)
    elif gate_type == "nor":
        builder.gate("nor", inputs, output)
    elif gate_type == "or":
        or_gate(inputs, output)
    elif gate_type == "and":
        and_gate(inputs, output)
    elif gate_type == "nand":
        inv(and_gate(inputs), output)
    elif gate_type == "xor" and len(inputs) == 2:
        a, b = inputs
        t1 = builder.gate("nor", [a, b])
        t2 = builder.gate("nor", [a, t1])
        t3 = builder.gate("nor", [b, t1])
        xnor = builder.gate("nor", [t2, t3])
        inv(xnor, output)
    elif gate_type == "xnor" and len(inputs) == 2:
        a, b = inputs
        t1 = builder.gate("nor", [a, b])
        t2 = builder.gate("nor", [a, t1])
        t3 = builder.gate("nor", [b, t1])
        builder.gate("nor", [t2, t3], output)
    else:
        raise ValueError(f"Cannot legalize {gate_type} with {len(inputs)} input(s) to NOR/NOT")


def _emit_xor_as_and_or_not(builder: _GateLibraryEmitter, a: str, b: str, output: str) -> None:
    not_a = builder.gate("not", [a])
    not_b = builder.gate("not", [b])
    t1 = builder.gate("and", [a, not_b])
    t2 = builder.gate("and", [not_a, b])
    builder.gate("or", [t1, t2], output)


def _emit_xor_as_and_not(builder: _GateLibraryEmitter, a: str, b: str, output: str) -> None:
    not_a = builder.gate("not", [a])
    not_b = builder.gate("not", [b])
    a_and_not_b = builder.gate("and", [a, not_b])
    not_a_and_b = builder.gate("and", [not_a, b])
    not_term_a = builder.gate("not", [a_and_not_b])
    not_term_b = builder.gate("not", [not_a_and_b])
    mid = builder.gate("and", [not_term_a, not_term_b])
    builder.gate("not", [mid], output)
