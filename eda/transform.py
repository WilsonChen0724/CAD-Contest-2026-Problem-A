from __future__ import annotations

from eda.design import Design, Gate
from eda.graph import rebuild_graph


def replace_buffers_with_and(design: Design, targets: list[str], extra_input: str) -> dict:
    """
    Replace each target buf with an and gate.

    Original:
        buf U(out, in);

    New IR:
        and U(out, in, extra_input);
    """
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

    rebuild_graph(design)
    return {"changed": changed, "num_changed": len(changed)}


def remove_dangling(design: Design) -> dict:
    """
    Placeholder for Day4.

    Future implementation:
        Start from primary outputs, traverse fanin, keep reachable gates.
    """
    return {"removed_gates": [], "removed_nets": []}


def replace_inv_buf_with_inv(design: Design) -> dict:
    """
    Placeholder for Day4.
    """
    return {"changed": []}


def replace_or_with_nand_not(design: Design, cone_target: str) -> dict:
    """
    Placeholder for Day4.
    """
    return {"changed": [], "cone_target": cone_target}
