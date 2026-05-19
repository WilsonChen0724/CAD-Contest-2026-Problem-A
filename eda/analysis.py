from __future__ import annotations

from collections import deque
from eda.design import Design
from eda.graph import rebuild_graph


def find_gates(design: Design, gate_type: str | None = None, name_contains: str | None = None) -> list[str]:
    result = []
    for name, gate in design.gates.items():
        if gate_type is not None and gate.type != gate_type.lower():
            continue
        if name_contains is not None and name_contains not in name:
            continue
        result.append(name)
    return result


def find_path(design: Design, src: str, dst: str, avoid: list[str] | None = None) -> list[str]:
    """
    MVP graph search over alternating net/instance names.
    This is intentionally simple and will be improved after parser is ready.
    """
    rebuild_graph(design)
    avoid_set = set(avoid or [])
    q = deque([(src, [src])])
    seen = {src}

    while q:
        node, path = q.popleft()
        if node == dst:
            return path

        # If node is a net, traverse to gates that consume it and to PO.
        for sink in design.fanouts.get(node, []):
            _, sink_name = sink.split(":", 1)
            if sink_name in avoid_set:
                continue
            if sink.startswith("PO:"):
                nxt = sink_name
                if nxt not in seen:
                    seen.add(nxt)
                    q.append((nxt, path + [nxt]))
            elif sink.startswith("GATE:"):
                gate = design.gates[sink_name]
                nxt = gate.output
                if gate.name not in seen:
                    seen.add(gate.name)
                    q.append((nxt, path + [gate.name, nxt]))

    return []

#----------DAG + DP for longest path from src to dst. In a DAG, this is guaranteed to terminate and yield the correct result.
def max_depth(design: Design, src: str, dst: str) -> tuple[int, list[str]]:
    """
    Compute the maximum combinational gate depth from src to dst.

    The traversal relaxes depths over the fanout graph. Nets carry a depth, and
    crossing a primitive gate adds one. DFF sinks are intentionally not traversed
    because they are sequential boundaries in docs/tool_spec.md.
    """
    rebuild_graph(design)
    if src == dst:
        return 0, [src]

    best_depth: dict[str, int] = {src: 0}
    best_path: dict[str, list[str]] = {src: [src]}
    q = deque([src])

    # In a DAG, each net's best depth can improve only a bounded number of
    # times. This guard prevents an accidental combinational loop from turning
    # the longest-path relaxation into an infinite loop.
    relax_limit = max(1, len(design.gates) + len(design.wires) + len(design.outputs) + len(design.inputs))
    relax_count: dict[str, int] = {}

    while q:
        net = q.popleft()
        current_depth = best_depth[net]
        current_path = best_path[net]

        for sink in design.fanouts.get(net, []):
            if not sink.startswith("GATE:"):
                continue

            gate_name = sink.split(":", 1)[1]
            gate = design.gates[gate_name]
            out_net = gate.output
            candidate_depth = current_depth + 1
            if candidate_depth <= best_depth.get(out_net, -1):
                continue

            best_depth[out_net] = candidate_depth
            best_path[out_net] = current_path + [gate.name, out_net]
            relax_count[out_net] = relax_count.get(out_net, 0) + 1
            if relax_count[out_net] > relax_limit:
                raise ValueError("Combinational loop detected while computing max depth.")
            q.append(out_net)

    if dst not in best_depth:
        return 0, []
    return best_depth[dst], best_path[dst]


def logic_cone(design: Design, target: str) -> list[str]:
    """
    Return gate names in transitive fanin cone of target.
    """
    rebuild_graph(design)
    result: set[str] = set()
    stack = [target]

    while stack:
        net = stack.pop()
        driver = design.drivers.get(net)
        if not driver or not driver.startswith("GATE:"):
            continue
        gate_name = driver.split(":", 1)[1]
        if gate_name in result:
            continue
        result.add(gate_name)
        gate = design.gates[gate_name]
        stack.extend(gate.inputs)

    return sorted(result)
