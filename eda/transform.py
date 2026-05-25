from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from eda.design import Design
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
    return {"removed_gates": [], "removed_nets": []}


@_rebuild_graph_after_transform
def replace_inv_buf_with_inv(design: Design) -> dict:
    """Collapse safe inverter-buffer chains into one inverter."""
    return {"changed": []}


@_rebuild_graph_after_transform
def replace_or_with_nand_not(design: Design, cone_target: str) -> dict:
    """Rewrite OR gates in a cone using equivalent NAND/NOT logic."""
    return {"changed": [], "cone_target": cone_target}
