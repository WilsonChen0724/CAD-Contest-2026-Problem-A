from __future__ import annotations

from collections.abc import Callable, Sequence
from math import ceil
from functools import wraps
from typing import Any, TypeVar

from eda.analysis import logic_cone
from eda.design import DFF, Design, Gate, is_constant
from eda.graph import rebuild_graph

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

    for gate_name in sorted(cone_gates):
        gate = design.gates.get(gate_name)
        if gate is None or gate.type != "or" or len(gate.inputs) != 2:
            continue

        input_a, input_b = gate.inputs
        not_a_net = design.make_unique_wire_name(f"{gate.name}_na")
        not_b_net = design.make_unique_wire_name(f"{gate.name}_nb")
        not_a_name = design.make_unique_gate_name(f"{gate.name}_not_a")
        not_b_name = design.make_unique_gate_name(f"{gate.name}_not_b")

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
        buffer_name = design.make_unique_gate_name(f"{net}_fanout_buf")
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
        "final_max_fanout": _max_fanout(design),
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


def _max_fanout(design: Design) -> int:
    rebuild_graph(design)
    return max((len(sinks) for sinks in design.fanouts.values()), default=0)
