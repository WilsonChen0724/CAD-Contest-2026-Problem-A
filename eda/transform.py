from __future__ import annotations

from collections.abc import Callable, Sequence
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
    """Collapse safe NOT->NOT chains by reconnecting downstream sinks."""
    changed: list[dict[str, Any]] = []
    max_iterations = max(1, len(design.gates) + 1)

    for _ in range(max_iterations):
        rebuild_graph(design)
        rewrite = _collapse_one_back_to_back_inverter(design)
        if rewrite is None:
            break
        changed.append(rewrite)
    else:
        raise RuntimeError("collapse_back_to_back_inverters did not converge.")

    return {"changed": changed, "num_changed": len(changed)}


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
    if max_depth is not None and final_depth > max_depth:
        raise ValueError(
            f'Optimized cone depth {final_depth} exceeds max_depth {max_depth} for target "{target}".'
        )

    final_gates = logic_cone(design, resolved_target)
    return {
        "target": target,
        "resolved_target": resolved_target,
        "target_resolution": resolved,
        "max_depth": max_depth,
        "initial_gate_count": len(initial_gates),
        "final_gate_count": len(final_gates),
        "removed_gate_count": len(initial_gates) - len(final_gates),
        "initial_depth": initial_depth,
        "final_depth": final_depth,
        "changed": changed,
        "num_changed": len(changed),
    }


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
    if max_outputs is None and (gate_count > 10000 or has_fanout_buffers):
        result = _optimize_design_depth_fast_cleanup(design, max_depth=max_depth)
        result["engine"] = "large_design_bounded_cleanup"
        result["fallback_reason"] = "Skipped full-design Yosys/ABC for a large or fanout-buffered design."
        return result

    if max_outputs is None and gate_count > 2000:
        return _optimize_design_depth_locally(
            design,
            max_depth=max_depth,
            max_outputs=16,
            engine="topk_critical_cones",
            bounded_reason="medium design: optimized the largest critical cones only",
        )

    try:
        return _optimize_design_depth_with_yosys_abc(design, max_depth=max_depth)
    except Exception as exc:
        result = _optimize_design_depth_locally(design, max_depth=max_depth, max_outputs=max_outputs)
        result["engine"] = "local_fallback"
        result["fallback_reason"] = str(exc)
        return result


@_rebuild_graph_after_transform
def replace_xnor_nor_with_basic_gates(design: Design) -> dict:
    """Rewrite XNOR and NOR gates as XOR/NOT and OR/NOT structures."""
    changed: list[dict[str, Any]] = []
    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None or gate.type not in {"xnor", "nor"} or len(gate.inputs) != 2:
            continue
        old_type = gate.type
        mid_net = design.make_unique_wire_name(f"{name}_{old_type}_pre")
        out_net = gate.output
        gate.output = mid_net
        gate.type = "xor" if old_type == "xnor" else "or"
        inv_name = design.make_unique_gate_name(f"{name}_{old_type}_not")
        design.add_gate(Gate(name=inv_name, type="not", inputs=[mid_net], output=out_net))
        changed.append({"rewritten_gate": name, "old_type": old_type, "added_gate": inv_name, "added_net": mid_net})
    return {"changed": changed, "num_changed": len(changed)}


@_rebuild_graph_after_transform
def replace_and_not_with_nand(design: Design) -> dict:
    """Rewrite AND/NOT gates into NAND-only structures."""
    changed: list[dict[str, Any]] = []
    for name in sorted(list(design.gates)):
        gate = design.gates.get(name)
        if gate is None:
            continue
        if gate.type == "not" and len(gate.inputs) == 1:
            gate.type = "nand"
            gate.inputs = [gate.inputs[0], gate.inputs[0]]
            changed.append({"rewritten_gate": name, "old_type": "not", "added_gates": []})
        elif gate.type == "and" and len(gate.inputs) == 2:
            out_net = gate.output
            mid_net = design.make_unique_wire_name(f"{name}_nand_pre")
            gate.type = "nand"
            gate.output = mid_net
            inv_name = design.make_unique_gate_name(f"{name}_nand_restore")
            design.add_gate(Gate(name=inv_name, type="nand", inputs=[mid_net, mid_net], output=out_net))
            changed.append({"rewritten_gate": name, "old_type": "and", "added_gates": [inv_name], "added_nets": [mid_net]})
    return {"changed": changed, "num_changed": len(changed)}


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


def _collapse_one_back_to_back_inverter(design: Design) -> dict[str, Any] | None:
    for second_name in sorted(list(design.gates)):
        second = design.gates.get(second_name)
        if second is None or second.type != "not" or len(second.inputs) != 1:
            continue
        mid_net = second.inputs[0]
        driver = design.drivers.get(mid_net)
        if not driver or not driver.startswith("GATE:"):
            continue
        first_name = driver.split(":", 1)[1]
        first = design.gates.get(first_name)
        if first is None or first.type != "not" or len(first.inputs) != 1:
            continue
        if design.fanouts.get(mid_net, []) != [f"GATE:{second_name}"]:
            continue

        source_net = first.inputs[0]
        output_net = second.output
        if output_net in design.outputs or any(sink.startswith("PO:") for sink in design.fanouts.get(output_net, [])):
            second.type = "buf"
            second.inputs = [source_net]
            del design.gates[first_name]
            _discard_internal_wire(design, mid_net)
            return {
                "rule": "back_to_back_inverter_to_output_buffer",
                "removed_gates": [first_name],
                "rewritten_gate": second_name,
                "removed_nets": [mid_net],
            }

        for sink in list(design.fanouts.get(output_net, [])):
            _redirect_sink(design, sink, old_net=output_net, new_net=source_net)
        del design.gates[first_name]
        del design.gates[second_name]
        _discard_internal_wire(design, mid_net)
        _discard_internal_wire(design, output_net)
        return {
            "rule": "remove_back_to_back_inverters",
            "removed_gates": [first_name, second_name],
            "redirected_net": output_net,
            "replacement_net": source_net,
            "removed_nets": [mid_net, output_net],
        }
    return None


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


def _optimize_design_depth_with_yosys_abc(design: Design, max_depth: int | None = None) -> dict:
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        raw_input = tmp_dir / "input.v"
        yosys_input = tmp_dir / "input_for_abc.v"
        optimized_output = tmp_dir / "optimized.v"
        write_verilog(design, raw_input)
        _write_yosys_abc_input(raw_input, yosys_input)

        script = _build_yosys_abc_depth_script(
            input_path=yosys_input,
            output_path=optimized_output,
            top_module=design.module_name,
            max_depth=max_depth,
            use_delay_target=max_depth is not None,
        )
        completed = run_yosys_script(script, cwd=tmp_dir)
        if completed.returncode != 0 and max_depth is not None:
            script = _build_yosys_abc_depth_script(
                input_path=yosys_input,
                output_path=optimized_output,
                top_module=design.module_name,
                max_depth=max_depth,
                use_delay_target=False,
            )
            completed = run_yosys_script(script, cwd=tmp_dir)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown Yosys/ABC error").strip()
            raise RuntimeError(message)
        if not optimized_output.exists():
            raise RuntimeError("Yosys/ABC did not produce an optimized Verilog file.")

        optimized = parse_verilog(optimized_output)
        _prune_unreferenced_wires(optimized)
        connectivity = check_connectivity(optimized)
        if not connectivity.get("ok"):
            raise RuntimeError(f"Yosys/ABC optimized design failed connectivity check: {connectivity}")

    final_depth = _design_max_depth(optimized)
    final_gate_count = len(optimized.gates)
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
        "changed": [],
        "num_changed_outputs": 0 if initial_depth == final_depth and initial_gate_count == len(design.gates) else len(design.outputs),
    }


def _optimize_design_depth_locally(
    design: Design,
    max_depth: int | None = None,
    max_outputs: int | None = None,
    engine: str = "local",
    bounded_reason: str | None = None,
) -> dict:
    reports = _optimization_target_cone_sizes(design)
    outputs = [name for name, _ in sorted(reports.items(), key=lambda item: item[1]["num_gates"], reverse=True)]
    initial_gate_count = len(design.gates)
    initial_depth = _design_max_depth(design)

    if max_outputs is None and (len(outputs) > 128 or len(design.gates) > 2000):
        return _optimize_design_depth_with_bounded_cleanup(
            design,
            max_depth=max_depth,
            outputs=outputs,
            initial_gate_count=initial_gate_count,
            initial_depth=initial_depth,
        )

    selected_outputs = outputs[:max_outputs] if max_outputs is not None else outputs
    changed: list[dict[str, Any]] = []
    skipped_dynamic: list[dict[str, str]] = []

    for output in selected_outputs:
        if output not in design.all_nets():
            skipped_dynamic.append({"target": output, "reason": "target no longer exists after earlier local rewrites"})
            continue
        try:
            result = optimize_cone(design, output, max_depth=max_depth, minimize_gate_count=True)
        except ValueError as exc:
            skipped_dynamic.append({"target": output, "reason": str(exc)})
            continue
        if result.get("num_changed", 0):
            changed.append(result)

    final_depth = _design_max_depth(design)
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
    abc_pass = f"abc -D {max(1, max_depth or 1)}" if use_delay_target else "abc -fast"
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
    sources = _depth_sources(design)
    sinks = _depth_sinks(design)
    max_seen = 0
    for source in sorted(sources):
        for sink in sorted(sinks):
            depth, path = max_depth(design, source, sink)
            if path:
                max_seen = max(max_seen, depth)
    return max_seen


def _depth_sources(design: Design) -> set[str]:
    return set(design.inputs) | {dff.q for dff in design.dffs.values()}


def _depth_sinks(design: Design) -> set[str]:
    return set(design.outputs) | {dff.d for dff in design.dffs.values() if not is_constant(dff.d)}


def _optimization_target_cone_sizes(design: Design) -> dict[str, dict[str, Any]]:
    reports = primary_output_cone_sizes(design)
    for target in sorted({dff.d for dff in design.dffs.values() if not is_constant(dff.d)}):
        if target in reports:
            continue
        cone_gates = logic_cone(design, target)
        reports[target] = {
            "target": target,
            "num_gates": len(cone_gates),
            "num_nets": 0,
        }
    return reports
def _cone_max_depth(design: Design, target: str) -> int:
    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    depths = []
    for source in sorted(sources):
        depth, path = max_depth(design, source, target)
        if path:
            depths.append(depth)
    return max(depths, default=0)



