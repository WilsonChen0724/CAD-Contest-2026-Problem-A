from __future__ import annotations

from collections.abc import Callable, Sequence
from math import ceil
from functools import wraps
from typing import Any, TypeVar

from eda.analysis import find_path, logic_cone, max_depth
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
# day 6: balance_depth_with_buffers
# day 7: optimize_cone
# day 8: safe net rename

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
) -> dict:
    """
    Locally simplify a target fanin cone with function-preserving rewrites.

    First-version rules:
    - remove internal one-input buffers by reconnecting their sinks,
    - replace double inverters with a direct connection, or with one buffer if
      the second inverter drives a primary output net.
    """
    del minimize_gate_count  # The current rule set always reduces gate count when possible.
    if target not in design.all_nets():
        raise ValueError(f'Target net not found: "{target}"')
    if max_depth is not None and max_depth < 0:
        raise ValueError("optimize_cone requires max_depth >= 0 when provided.")

    rebuild_graph(design)
    initial_gates = logic_cone(design, target)
    initial_depth = _cone_max_depth(design, target)
    changed: list[dict[str, Any]] = []
    max_iterations = max(1, len(design.gates) + 1)

    for _ in range(max_iterations):
        cone_gates = set(logic_cone(design, target))
        rewrite = _simplify_double_inverter_in_cone(design, cone_gates)
        if rewrite is None:
            rewrite = _remove_internal_buffer_in_cone(design, cone_gates)
        if rewrite is None:
            break
        changed.append(rewrite)
        rebuild_graph(design)
    else:
        raise RuntimeError("optimize_cone did not converge.")

    final_depth = _cone_max_depth(design, target)
    if max_depth is not None and final_depth > max_depth:
        raise ValueError(
            f'Optimized cone depth {final_depth} exceeds max_depth {max_depth} for target "{target}".'
        )

    final_gates = logic_cone(design, target)
    return {
        "target": target,
        "max_depth": max_depth,
        "initial_gate_count": len(initial_gates),
        "final_gate_count": len(final_gates),
        "removed_gate_count": len(initial_gates) - len(final_gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "changed": changed,
        "num_changed": len(changed),
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


def _cone_max_depth(design: Design, target: str) -> int:
    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    depths = []
    for source in sorted(sources):
        depth, path = max_depth(design, source, target)
        if path:
            depths.append(depth)
    return max(depths, default=0)
