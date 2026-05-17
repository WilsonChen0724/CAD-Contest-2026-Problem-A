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


def max_depth(design: Design, src: str, dst: str) -> tuple[int, list[str]]:
    """
    MVP placeholder.

    Return:
        (depth, example_path)

    Day2/Day3 task:
        Replace this with DAG DP/topological traversal.
    """
    path = find_path(design, src, dst)
    depth = sum(1 for x in path if x in design.gates)
    return depth, path


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
