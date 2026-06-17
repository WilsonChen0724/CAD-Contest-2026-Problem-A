from __future__ import annotations

from collections import deque
from eda.design import Design
from eda.graph import rebuild_graph

_GATE_COUNT_ORDER = ["and", "or", "not", "nand", "nor", "xor", "xnor", "buf", "dff"]


def find_gates(design: Design, gate_type: str | None = None, name_contains: str | None = None) -> list[str]:
    result = []
    normalized_type = gate_type.lower() if gate_type is not None else None
    if normalized_type == "dff":
        for name in sorted(design.dffs):
            dff = design.dffs[name]
            if (
                name_contains is not None
                and name_contains not in name
                and name_contains not in {dff.d, dff.q, dff.clk, dff.rst}
            ):
                continue
            result.append(name)
        return result
    for name, gate in design.gates.items():
        if normalized_type is not None and gate.type != normalized_type:
            continue
        if name_contains is not None and name_contains not in name:
            continue
        result.append(name)
    return result


def gate_counts(design: Design) -> dict:
    """Count primitive gate instances by type, including DFF cells."""
    counts = {gate_type: 0 for gate_type in _GATE_COUNT_ORDER}
    for gate in design.gates.values():
        counts.setdefault(gate.type, 0)
        counts[gate.type] += 1
    counts["dff"] = len(design.dffs)
    return {
        "counts": {gate_type: counts.get(gate_type, 0) for gate_type in _GATE_COUNT_ORDER},
        "total": sum(counts.values()),
    }


def gate_type_count(design: Design, gate_type: str) -> dict:
    """Count instances of one primitive gate type or DFF cell type."""
    normalized = gate_type.lower()
    counts = gate_counts(design)["counts"]
    return {
        "gate_type": normalized,
        "count": counts.get(normalized, 0),
    }

def io_counts(design: Design) -> dict:
    """Report primary input and primary output counts."""
    return {
        "num_inputs": len(design.inputs),
        "num_outputs": len(design.outputs),
        "inputs": sorted(design.inputs),
        "outputs": sorted(design.outputs),
    }


def primary_inputs_with_widths(design: Design) -> dict:
    """Group bit-expanded primary input names back into bus-width reports."""
    groups: dict[str, set[int] | None] = {}
    for net in design.inputs:
        base, bit = _split_bit_name(net)
        if bit is None:
            groups.setdefault(base, None)
            continue
        if groups.get(base) is None and base in groups:
            continue
        groups.setdefault(base, set())
        assert groups[base] is not None
        groups[base].add(bit)

    inputs = []
    for base in sorted(groups):
        bits = groups[base]
        if not bits:
            inputs.append({"name": base, "width": 1, "range": None, "bits": [base]})
            continue
        high = max(bits)
        low = min(bits)
        inputs.append(
            {
                "name": base,
                "width": high - low + 1,
                "range": f"[{high}:{low}]",
                "bits": [f"{base}[{index}]" for index in sorted(bits)],
            }
        )
    return {"num_inputs": len(inputs), "inputs": inputs}


def primary_outputs_with_widths(design: Design) -> dict:
    """Group bit-expanded primary output names back into bus-width reports."""
    groups: dict[str, set[int] | None] = {}
    for net in design.outputs:
        base, bit = _split_bit_name(net)
        if bit is None:
            groups.setdefault(base, None)
            continue
        if groups.get(base) is None and base in groups:
            continue
        groups.setdefault(base, set())
        assert groups[base] is not None
        groups[base].add(bit)

    outputs = []
    for base in sorted(groups):
        bits = groups[base]
        if not bits:
            outputs.append({"name": base, "width": 1, "range": None, "bits": [base]})
            continue
        high = max(bits)
        low = min(bits)
        outputs.append(
            {
                "name": base,
                "width": high - low + 1,
                "range": f"[{high}:{low}]",
                "bits": [f"{base}[{index}]" for index in sorted(bits)],
            }
        )
    return {"num_outputs": len(outputs), "outputs": outputs}


def direct_pi_to_po_paths(design: Design) -> dict:
    """Report zero-gate PI-to-PO paths represented by the same net name."""
    direct = sorted(set(design.inputs) & set(design.outputs))
    return {
        "num_paths": len(direct),
        "paths": [{"src": net, "dst": net, "path": [net]} for net in direct],
    }


def enumerate_paths(design: Design, src: str, dst: str, max_paths: int = 100) -> dict:
    """Enumerate simple combinational paths with a cap for large netlists."""
    rebuild_graph(design)
    if max_paths <= 0:
        raise ValueError("max_paths must be positive.")

    paths: list[list[str]] = []
    stack = [(src, [src], {src})]
    while stack and len(paths) < max_paths:
        net, path, seen_nets = stack.pop()
        if net == dst:
            paths.append(path)
            continue
        for sink in reversed(design.fanouts.get(net, [])):
            if sink.startswith("PO:"):
                output = sink.split(":", 1)[1]
                if output == dst:
                    paths.append(path + [output])
                    if len(paths) >= max_paths:
                        break
                continue
            if not sink.startswith("GATE:"):
                continue
            gate = design.gates[sink.split(":", 1)[1]]
            if gate.output in seen_nets:
                continue
            stack.append((gate.output, path + [gate.name, gate.output], seen_nets | {gate.output}))
    return {"src": src, "dst": dst, "paths": paths, "num_paths": len(paths), "truncated": bool(stack)}


def output_with_deepest_fanin_cone(design: Design) -> dict:
    """Report the primary output with the greatest structural logic depth."""
    report = outputs_depth_greater_than(design, -1)
    outputs = report["outputs"]
    if not outputs:
        return {"outputs": [], "max_depth": 0}
    max_depth_value = max(item["depth"] for item in outputs)
    deepest = [item for item in outputs if item["depth"] == max_depth_value]
    return {"outputs": deepest, "max_depth": max_depth_value}


def design_max_logic_depth(design: Design) -> dict:
    """Report the maximum combinational depth from PI/DFF-Q to PO/DFF-D."""
    rebuild_graph(design)
    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    prefix_depth = {source: 0 for source in sources}
    prefix_depth.update({"1'b0": 0, "1'b1": 0, "0": 0, "1": 0})
    prefix_path = {source: [source] for source in sources}
    _relax_forward_depths(design, prefix_depth, prefix_path)

    endpoints = sorted(set(design.outputs) | {dff.d for dff in design.dffs.values()})
    reachable = [(prefix_depth[net], net) for net in endpoints if net in prefix_depth]
    if not reachable:
        return {"max_depth": 0, "endpoint": None, "path": []}
    max_depth_value, endpoint = max(reachable)
    return {
        "max_depth": max_depth_value,
        "endpoint": endpoint,
        "path": prefix_path.get(endpoint, [endpoint]),
    }


def cone_depth(design: Design, target: str) -> dict:
    """Report the maximum structural depth inside one fanin cone."""
    rebuild_graph(design)
    target_net = design.gates[target].output if target in design.gates else target
    boundary_nets = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    boundary_nets.update({"1'b0", "1'b1", "0", "1"})
    memo: dict[str, tuple[int, list[str]]] = {}
    visiting: set[str] = set()

    def visit(net: str) -> tuple[int, list[str]]:
        if net in memo:
            return memo[net]
        if net in boundary_nets:
            memo[net] = (0, [net])
            return memo[net]

        driver = design.drivers.get(net)
        if not driver or not driver.startswith("GATE:"):
            memo[net] = (0, [net])
            return memo[net]
        if net in visiting:
            raise ValueError("Combinational loop detected while computing cone depth.")

        visiting.add(net)
        gate = design.gates[driver.split(":", 1)[1]]
        input_results = [visit(input_net) for input_net in gate.inputs]
        visiting.remove(net)

        if not input_results:
            memo[net] = (1, [gate.name, net])
            return memo[net]
        best_depth, best_path = max(input_results, key=lambda item: item[0])
        memo[net] = (best_depth + 1, best_path + [gate.name, net])
        return memo[net]

    depth, path = visit(target_net)
    return {
        "target": target,
        "net": target_net,
        "max_depth": depth,
        "path": path,
        "num_gates": len(logic_cone(design, target_net)),
    }


def max_register_to_register_depth(design: Design) -> dict:
    """Report maximum combinational depth from any DFF Q pin to any DFF D pin."""
    rebuild_graph(design)
    if not design.dffs:
        return {"max_depth": 0, "src_dff": None, "dst_dff": None, "path": []}

    q_to_dff = {dff.q: name for name, dff in design.dffs.items()}
    sources = set(design.inputs) | set(q_to_dff) | {"1'b0", "1'b1", "0", "1"}
    prefix_depth = {net: 0 for net in sources}
    prefix_path: dict[str, list[str]] = {net: [net] for net in sources}
    _relax_forward_depths(design, prefix_depth, prefix_path)

    best: tuple[int, str | None, str | None, list[str]] = (0, None, None, [])
    for dst_name, dst_dff in sorted(design.dffs.items()):
        depth = prefix_depth.get(dst_dff.d)
        if depth is None:
            continue
        path = prefix_path.get(dst_dff.d, [dst_dff.d])
        src_name = q_to_dff.get(path[0])
        if src_name == dst_name:
            continue
        if depth > best[0]:
            best = (depth, src_name, dst_name, path)

    return {
        "max_depth": best[0],
        "src_dff": best[1],
        "dst_dff": best[2],
        "path": best[3],
    }


def gates_by_type(design: Design, gate_type: str, limit: int | None = 200) -> dict:
    """Report gates of one primitive type with pin summaries."""
    normalized = gate_type.lower()
    if normalized == "dff":
        rows = [
            {"name": name, "type": "dff", "inputs": [dff.d, dff.clk] + ([dff.rst] if dff.rst else []), "output": dff.q}
            for name, dff in sorted(design.dffs.items())
        ]
    else:
        rows = [
            {"name": name, "type": gate.type, "inputs": list(gate.inputs), "output": gate.output}
            for name, gate in sorted(design.gates.items())
            if gate.type == normalized
        ]
    selected = rows if limit is None else rows[:limit]
    return {
        "gate_type": normalized,
        "num_gates": len(rows),
        "gates": selected,
        "limit": limit,
        "truncated": limit is not None and len(rows) > limit,
    }


def cut_signal_between_pi_po(design: Design, signal: str, max_pairs_checked: int = 200) -> dict:
    """
    Check if a signal is a structural cut for at least one PI-to-PO pair.

    The criterion is: a PI can reach the signal, the signal can reach a PO, and
    every path for at least one such PI/PO pair passes through the signal.
    """
    rebuild_graph(design)
    if signal not in design.all_nets() and signal not in design.gates:
        return {"signal": signal, "is_cut": False, "reason": "signal not found"}

    checked = 0
    for src in sorted(design.inputs):
        if src == signal:
            reaches_signal = True
        else:
            reaches_signal = bool(find_path(design, src, signal))
        if not reaches_signal:
            continue
        for dst in sorted(design.outputs):
            if not find_path(design, signal, dst) and signal != dst:
                continue
            checked += 1
            if checked > max_pairs_checked:
                return {
                    "signal": signal,
                    "is_cut": False,
                    "checked_pairs": checked - 1,
                    "truncated": True,
                    "reason": "pair search limit reached before finding a cut pair",
                }
            if all_paths_pass_through(design, src, dst, signal):
                return {
                    "signal": signal,
                    "is_cut": True,
                    "src": src,
                    "dst": dst,
                    "checked_pairs": checked,
                    "truncated": False,
                }
    return {"signal": signal, "is_cut": False, "checked_pairs": checked, "truncated": False}


def direct_fanout(design: Design, net: str) -> dict:
    """Report direct loads driven by one net or one gate/DFF instance."""
    rebuild_graph(design)
    source = net
    source_kind = "net"
    if net in design.gates:
        source_kind = "gate"
        net = design.gates[net].output
    elif net in design.dffs:
        source_kind = "dff"
        net = design.dffs[net].q
    elif net not in design.all_nets():
        raise ValueError(f'Net or instance not found: "{source}"')

    sinks = design.fanouts.get(net, [])
    sink_reports = [_describe_sink(design, net, sink) for sink in sinks]
    unique_sinks = sorted({item["sink"] for item in sink_reports})
    return {
        "source": source,
        "source_kind": source_kind,
        "net": net,
        "num_loads": len(sinks),
        "num_unique_sinks": len(unique_sinks),
        "num_gate_sinks": sum(1 for item in sink_reports if item["kind"] == "gate"),
        "num_dff_sinks": sum(1 for item in sink_reports if item["kind"] == "dff"),
        "num_primary_output_sinks": sum(1 for item in sink_reports if item["kind"] == "primary_output"),
        "sinks": sink_reports,
    }


def highest_fanout_primary_input(design: Design) -> dict:
    """Return the primary input or inputs with the largest direct fanout."""
    rebuild_graph(design)
    fanouts = {name: len(design.fanouts.get(name, [])) for name in sorted(design.inputs)}
    if not fanouts:
        return {"max_fanout": 0, "inputs": [], "fanouts": {}}
    max_fanout = max(fanouts.values())
    return {
        "max_fanout": max_fanout,
        "inputs": [name for name, count in fanouts.items() if count == max_fanout],
        "fanouts": fanouts,
    }


def gate_type_connections(design: Design, gate_type: str, max_items: int | None = 200) -> dict:
    """Report connections for gates of one type."""
    normalized = gate_type.lower()
    names = sorted(design.dffs) if normalized == "dff" else [
        name for name, gate in sorted(design.gates.items()) if gate.type == normalized
    ]
    selected = names if max_items is None else names[:max_items]
    rows = []
    for name in selected:
        if normalized == "dff":
            dff = design.dffs[name]
            pins = {"D": dff.d, "Q": dff.q, "CLK": dff.clk}
            if dff.rst is not None:
                pins["RST"] = dff.rst
            rows.append({"kind": "dff", "instance": name, "pins": pins})
        else:
            gate = design.gates[name]
            rows.append(
                {
                    "kind": "gate",
                    "instance": name,
                    "inputs": list(gate.inputs),
                    "output": gate.output,
                }
            )
    return {
        "gate_type": normalized,
        "num_gates": len(names),
        "max_items": max_items,
        "truncated": max_items is not None and len(names) > len(selected),
        "gates": rows,
    }


def dffs_by_clock(design: Design, clock: str, max_items: int | None = 200) -> dict:
    """Report DFF instances driven by one clock net."""
    matched = [name for name, dff in sorted(design.dffs.items()) if dff.clk == clock]
    selected = matched if max_items is None else matched[:max_items]
    rows = []
    for name in selected:
        dff = design.dffs[name]
        pins = {"D": dff.d, "Q": dff.q, "CLK": dff.clk}
        if dff.rst is not None:
            pins["RST"] = dff.rst
        rows.append({"instance": name, "pins": pins})
    return {
        "clock": clock,
        "num_dffs": len(matched),
        "max_items": max_items,
        "truncated": max_items is not None and len(matched) > len(selected),
        "dffs": rows,
    }


def direct_pi_po_paths(design: Design) -> dict:
    """Report zero-gate direct wire paths from primary inputs to primary outputs."""
    direct = sorted(output for output in design.outputs if output in design.inputs)
    return {
        "num_paths": len(direct),
        "paths": [[net, net] for net in direct],
    }


def gate_connections(design: Design, gate_name: str) -> dict:
    """Report one gate or DFF instance's pins plus direct output fanout."""
    rebuild_graph(design)
    if gate_name in design.gates:
        gate = design.gates[gate_name]
        return {
            "instance": gate_name,
            "kind": "gate",
            "gate_type": gate.type,
            "inputs": list(gate.inputs),
            "output": gate.output,
            "output_fanout": [_describe_sink(design, gate.output, sink) for sink in design.fanouts.get(gate.output, [])],
        }
    if gate_name in design.dffs:
        dff = design.dffs[gate_name]
        pins = {"D": dff.d, "Q": dff.q, "CLK": dff.clk, "RST": dff.rst}
        return {
            "instance": gate_name,
            "kind": "dff",
            "gate_type": "dff",
            "pins": pins,
            "output": dff.q,
            "output_fanout": [_describe_sink(design, dff.q, sink) for sink in design.fanouts.get(dff.q, [])],
        }
    raise ValueError(f'Gate or DFF not found: "{gate_name}"')


def find_path(design: Design, src: str, dst: str, avoid: list[str] | None = None) -> list[str]:
    """
    MVP graph search over alternating net/instance names.
    This is intentionally simple and will be improved after parser is ready.
    """
    rebuild_graph(design)
    avoid_set = set(avoid or [])
    src_candidates = _resolve_signal_candidates(design, src)
    dst_candidates = set(_resolve_signal_candidates(design, dst))
    if not src_candidates or not dst_candidates:
        return []
    q = deque((candidate, [candidate]) for candidate in src_candidates)
    seen = set(src_candidates)

    while q:
        node, path = q.popleft()
        if node in avoid_set and node != src:
            continue
        if node in dst_candidates:
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
                if nxt in avoid_set:
                    continue
                if gate.name not in seen:
                    seen.add(gate.name)
                    q.append((nxt, path + [gate.name, nxt]))

    return []


def articulation_points_between(design: Design, src: str, dst: str) -> dict:
    """
    Report articulation points in the combinational graph between src and dst.

    The graph is built from alternating net and gate-instance nodes and then
    restricted to nodes that can lie on a src-to-dst combinational path.
    """
    rebuild_graph(design)
    adjacency = _combinational_adjacency(design)
    reverse = _reverse_adjacency(adjacency)
    forward = _reachable_nodes(adjacency, src)
    backward = _reachable_nodes(reverse, dst)
    sub_nodes = (forward & backward) | {src, dst}
    if dst not in forward:
        return {"src": src, "dst": dst, "articulation_points": [], "num_points": 0}

    undirected: dict[str, set[str]] = {node: set() for node in sub_nodes}
    for node in sub_nodes:
        for nxt in adjacency.get(node, set()):
            if nxt in sub_nodes:
                undirected[node].add(nxt)
                undirected[nxt].add(node)

    points = sorted(point for point in _articulation_points(undirected, src) if point not in {src, dst})
    return {"src": src, "dst": dst, "articulation_points": points, "num_points": len(points)}


def _combinational_adjacency(design: Design) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}

    def add_edge(a: str, b: str) -> None:
        adjacency.setdefault(a, set()).add(b)

    for gate in design.gates.values():
        for input_net in gate.inputs:
            add_edge(input_net, gate.name)
        add_edge(gate.name, gate.output)
    for output in design.outputs:
        driver = design.drivers.get(output)
        if driver and driver.startswith("GATE:"):
            add_edge(driver.split(":", 1)[1], output)
        elif output in design.inputs:
            add_edge(output, output)
    return adjacency


def _reverse_adjacency(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for node, next_nodes in adjacency.items():
        reverse.setdefault(node, set())
        for nxt in next_nodes:
            reverse.setdefault(nxt, set()).add(node)
    return reverse


def _reachable_nodes(adjacency: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in adjacency.get(node, set()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _articulation_points(graph: dict[str, set[str]], start: str) -> set[str]:
    index = 0
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {start: None}
    points: set[str] = set()

    def dfs(node: str) -> None:
        nonlocal index
        order[node] = index
        low[node] = index
        index += 1
        child_count = 0

        for nxt in sorted(graph.get(node, set())):
            if nxt not in order:
                parent[nxt] = node
                child_count += 1
                dfs(nxt)
                low[node] = min(low[node], low[nxt])
                if parent[node] is None and child_count > 1:
                    points.add(node)
                if parent[node] is not None and low[nxt] >= order[node]:
                    points.add(node)
            elif nxt != parent.get(node):
                low[node] = min(low[node], order[nxt])

    dfs(start)
    return points


def all_paths_pass_through(design: Design, src: str, dst: str, node: str) -> bool:
    """Return True when every combinational path from src to dst crosses node."""
    if src == dst:
        return src == node
    if not find_path(design, src, dst):
        return False
    return not find_path(design, src, dst, avoid=[node])


def all_paths(design: Design, src: str, dst: str, max_paths: int = 200) -> dict:
    """Enumerate bounded combinational paths from src to dst."""
    if max_paths < 1:
        raise ValueError("max_paths must be at least 1.")
    rebuild_graph(design)
    adjacency = _combinational_adjacency(design)
    src_candidates = _resolve_signal_candidates(design, src)
    dst_candidates = set(_resolve_signal_candidates(design, dst))
    if not src_candidates or not dst_candidates:
        return {
            "src": src,
            "dst": dst,
            "paths": [],
            "num_paths": 0,
            "max_paths": max_paths,
            "truncated": False,
        }
    reverse = _reverse_adjacency(adjacency)
    can_reach_dst: set[str] = set()
    for candidate in dst_candidates:
        can_reach_dst.update(_reachable_nodes(reverse, candidate))
    can_reach_dst.update(dst_candidates)
    paths: list[list[str]] = []
    truncated = False
    expansions = 0
    expansion_limit = max(10000, max_paths * 500)

    def dfs(node: str, path: list[str], active: set[str]) -> None:
        nonlocal expansions, truncated
        if truncated:
            return
        expansions += 1
        if expansions > expansion_limit:
            truncated = True
            return
        if node in dst_candidates:
            paths.append(path)
            if len(paths) >= max_paths:
                truncated = True
            return
        for nxt in sorted(adjacency.get(node, set())):
            if nxt in active or nxt not in can_reach_dst:
                continue
            dfs(nxt, path + [nxt], active | {nxt})
            if truncated:
                return

    for start in src_candidates:
        if start not in can_reach_dst and start not in dst_candidates:
            continue
        dfs(start, [start], {start})
        if truncated:
            break
    return {
        "src": src,
        "dst": dst,
        "paths": paths,
        "num_paths": len(paths),
        "max_paths": max_paths,
        "truncated": truncated,
    }


def _resolve_signal_candidates(design: Design, name: str) -> list[str]:
    """Resolve an exact net name or a bus base like n25 to expanded bit nets."""
    all_nets = design.all_nets()
    if name in all_nets:
        return [name]
    prefix = f"{name}["
    return sorted(net for net in all_nets if net.startswith(prefix))

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


def fanout_cone(design: Design, source: str) -> dict:
    """
    Return the transitive combinational fanout cone from a source net.

    Traversal crosses primitive gates from input nets to output nets, records
    reached primary outputs, and treats DFF inputs as sequential endpoints.
    """
    rebuild_graph(design)
    gates: set[str] = set()
    nets: set[str] = set()
    primary_outputs: set[str] = set()
    dff_sinks: set[str] = set()
    stack = [source]
    seen_nets = {source}

    while stack:
        net = stack.pop()
        nets.add(net)
        for sink in design.fanouts.get(net, []):
            if sink.startswith("PO:"):
                primary_outputs.add(sink.split(":", 1)[1])
                continue
            if sink.startswith("DFF:"):
                dff_sinks.add(sink.split(":", 1)[1])
                continue
            if not sink.startswith("GATE:"):
                continue

            gate_name = sink.split(":", 1)[1]
            if gate_name in gates:
                continue
            gates.add(gate_name)
            out_net = design.gates[gate_name].output
            if out_net not in seen_nets:
                seen_nets.add(out_net)
                stack.append(out_net)

    return {
        "source": source,
        "gates": sorted(gates),
        "nets": sorted(nets),
        "primary_outputs": sorted(primary_outputs),
        "dff_sinks": sorted(dff_sinks),
        "num_gates": len(gates),
        "num_nets": len(nets),
        "num_primary_outputs": len(primary_outputs),
        "num_dff_sinks": len(dff_sinks),
    }


def constant_input_gates(design: Design, gate_type: str | None = None) -> dict:
    """Report gates that have at least one constant input."""
    result = []
    for gate in design.gates.values():
        if gate_type is not None and gate.type != gate_type.lower():
            continue
        constants = [net for net in gate.inputs if net in {"1'b0", "1'b1", "0", "1"}]
        if not constants:
            continue
        result.append(
            {
                "name": gate.name,
                "gate_type": gate.type,
                "output": gate.output,
                "inputs": list(gate.inputs),
                "constant_inputs": constants,
            }
        )
    return {
        "gate_type": gate_type,
        "gates": sorted(result, key=lambda item: item["name"]),
        "num_gates": len(result),
    }


def primary_output_cone_sizes(design: Design) -> dict[str, dict]:
    """Report fanin cone size for each primary output."""
    rebuild_graph(design)
    report: dict[str, dict] = {}
    for output in sorted(design.outputs):
        gates = logic_cone(design, output)
        nets = _fanin_cone_nets(design, output)
        report[output] = {
            "num_gates": len(gates),
            "num_nets": len(nets),
            "gates": gates,
            "nets": sorted(nets),
        }
    return report


def largest_fanin_cone_output(design: Design) -> dict:
    """Report the primary output(s) with the largest structural fanin cone."""
    report = primary_output_cone_sizes(design)
    if not report:
        return {"max_gates": 0, "outputs": []}
    max_gates = max(item["num_gates"] for item in report.values())
    outputs = [
        {"output": output, "num_gates": item["num_gates"], "num_nets": item["num_nets"]}
        for output, item in sorted(report.items())
        if item["num_gates"] == max_gates
    ]
    return {"max_gates": max_gates, "outputs": outputs}


def shared_fanin_cone_gates(design: Design, target_a: str, target_b: str) -> dict:
    """Report gates shared by the transitive fanin cones of two targets."""
    cone_a = set(logic_cone(design, target_a))
    cone_b = set(logic_cone(design, target_b))
    shared = sorted(cone_a & cone_b)
    return {
        "target_a": target_a,
        "target_b": target_b,
        "num_shared_gates": len(shared),
        "shared_gates": shared,
    }


def gate_type_count_in_cone(design: Design, target: str, gate_type: str) -> dict:
    """Count gates of one primitive type in a target's fanin cone."""
    normalized = gate_type.lower()
    cone = logic_cone(design, target)
    matched = sorted(name for name in cone if design.gates[name].type == normalized)
    return {
        "target": target,
        "gate_type": normalized,
        "num_gates": len(matched),
        "gates": matched,
        "total_cone_gates": len(cone),
    }


def find_nand_equivalent_pair(
    design: Design,
    target: str,
    max_candidates: int = 80,
    max_pairs: int = 3000,
) -> dict:
    """
    Find whether NAND(a, b) matches a target signal.

    The search is deliberately bounded: first check existing NAND outputs
    structurally, then try pairs from the target fanin cone with formal
    equivalence when available.
    """
    rebuild_graph(design)
    for gate in sorted(design.gates.values(), key=lambda item: item.name):
        if gate.type == "nand" and len(gate.inputs) == 2 and gate.output == target:
            return {
                "target": target,
                "found": True,
                "a": gate.inputs[0],
                "b": gate.inputs[1],
                "method": "existing_nand_driver",
                "gate": gate.name,
                "pairs_checked": 1,
                "truncated": False,
            }

    cone_gates = logic_cone(design, target)
    candidate_nets: set[str] = set()
    for gate_name in cone_gates:
        gate = design.gates[gate_name]
        candidate_nets.add(gate.output)
        candidate_nets.update(gate.inputs)
    candidate_nets.discard(target)
    candidate_nets = {
        net
        for net in candidate_nets
        if net in design.all_nets() and net not in {"1'b0", "1'b1", "0", "1"}
    }
    candidates = sorted(candidate_nets)
    truncated_candidates = len(candidates) > max_candidates
    candidates = candidates[:max_candidates]

    pairs_checked = 0
    inconclusive_reason = None
    from eda.verify import check_equivalence

    for index, net_a in enumerate(candidates):
        for net_b in candidates[index:]:
            pairs_checked += 1
            if pairs_checked > max_pairs:
                return {
                    "target": target,
                    "found": False,
                    "pairs_checked": pairs_checked - 1,
                    "candidate_count": len(candidates),
                    "truncated": True,
                    "reason": "pair search limit reached",
                    "inconclusive_reason": inconclusive_reason,
                }
            try:
                result = check_equivalence(design, f"!({net_a} & {net_b})", target)
            except Exception as exc:  # Keep analysis robust on unusual cones.
                inconclusive_reason = str(exc)
                continue
            if result.get("ok"):
                return {
                    "target": target,
                    "found": True,
                    "a": net_a,
                    "b": net_b,
                    "method": "bounded_formal_cone_search",
                    "pairs_checked": pairs_checked,
                    "candidate_count": len(candidates),
                    "truncated": truncated_candidates,
                }
            if result.get("reason"):
                inconclusive_reason = result["reason"]

    return {
        "target": target,
        "found": False,
        "pairs_checked": pairs_checked,
        "candidate_count": len(candidates),
        "truncated": truncated_candidates,
        "inconclusive_reason": inconclusive_reason,
    }


def derive_boolean_equation(design: Design, target: str, max_terms: int = 200) -> dict:
    """
    Derive a structural Boolean expression for a target when tractable.

    Primary inputs are symbolic boundaries. If a target depends on a DFF Q pin,
    the expression follows the DFF D input so prompts asking for primary-input
    expressions do not stop at sequential state names. Sequential feedback is
    still guarded and replaced with the documented initial state 1'b0.
    """
    rebuild_graph(design)
    remaining = {"terms": max_terms}
    expression = _derive_expr(design, target, remaining, set())
    return {
        "target": target,
        "expression": expression,
        "truncated": remaining["terms"] <= 0,
    }


def max_depth_to_dff_d(design: Design) -> dict:
    """Compute maximum combinational depth from any primary input to any DFF D pin."""
    rebuild_graph(design)
    prefix_depth = {net: 0 for net in design.inputs}
    prefix_path: dict[str, list[str]] = {net: [net] for net in design.inputs}
    _relax_forward_depths(design, prefix_depth, prefix_path)

    best_depth = 0
    best_dff = None
    best_d_pin = None
    best_path: list[str] = []
    dff_depths: dict[str, int | None] = {}
    for name, dff in sorted(design.dffs.items()):
        depth = prefix_depth.get(dff.d)
        dff_depths[name] = depth
        if depth is not None and depth > best_depth:
            best_depth = depth
            best_dff = name
            best_d_pin = dff.d
            best_path = prefix_path.get(dff.d, [dff.d])

    return {
        "max_depth": best_depth,
        "dff": best_dff,
        "d_pin": best_d_pin,
        "path": best_path,
        "dff_depths": dff_depths,
    }


def outputs_depth_greater_than(design: Design, min_depth: int) -> dict:
    """Report primary outputs whose maximum structural logic depth exceeds a threshold."""
    rebuild_graph(design)
    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    prefix_depth = {net: 0 for net in sources}
    prefix_path: dict[str, list[str]] = {net: [net] for net in sources}
    _relax_forward_depths(design, prefix_depth, prefix_path)

    matched = []
    output_depths: dict[str, int | None] = {}
    for output in sorted(design.outputs):
        depth = prefix_depth.get(output)
        output_depths[output] = depth
        if depth is not None and depth > min_depth:
            matched.append({"output": output, "depth": depth, "path": prefix_path.get(output, [output])})

    return {
        "min_depth": min_depth,
        "num_outputs": len(matched),
        "outputs": matched,
        "output_depths": output_depths,
    }


def gate_on_max_depth_path(design: Design, gate_name: str) -> dict:
    """
    Check whether a gate lies on any global maximum combinational-depth path.

    Sources are primary inputs and DFF Q pins. Endpoints are primary outputs and
    DFF D pins. Missing-driver nets are treated as boundary sources so partially
    specified contest netlists can still be analyzed structurally.
    """
    rebuild_graph(design)
    if gate_name not in design.gates:
        raise ValueError(f'Gate not found: "{gate_name}"')

    sources = set(design.inputs) | {dff.q for dff in design.dffs.values()}
    sources.update(
        net
        for net in design.all_nets()
        if net not in design.drivers and net not in design.outputs
    )

    prefix_depth = {net: 0 for net in sources}
    prefix_path: dict[str, list[str]] = {net: [net] for net in sources}
    _relax_forward_depths(design, prefix_depth, prefix_path)

    endpoints = set(design.outputs) | {dff.d for dff in design.dffs.values()}
    reachable_endpoints = {
        net: depth for net, depth in prefix_depth.items() if net in endpoints
    }
    global_depth = max(reachable_endpoints.values(), default=0)
    endpoint = min(
        (net for net, depth in reachable_endpoints.items() if depth == global_depth),
        default=None,
    )

    suffix_depth = {net: 0 for net in endpoints}
    _relax_reverse_depths(design, suffix_depth)

    gate = design.gates[gate_name]
    output_suffix = suffix_depth.get(gate.output)
    input_candidates = [
        (prefix_depth[input_net], input_net)
        for input_net in gate.inputs
        if input_net in prefix_depth
    ]
    if output_suffix is None or not input_candidates:
        return {
            "gate": gate_name,
            "on_max_depth_path": False,
            "global_max_depth": global_depth,
            "example_endpoint": endpoint,
            "reason": "Gate is not on any source-to-endpoint combinational path.",
        }

    best_input_depth, best_input = max(input_candidates)
    gate_path_depth = best_input_depth + 1 + output_suffix
    on_path = gate_path_depth == global_depth
    example_path = []
    if on_path:
        example_path = prefix_path.get(best_input, [best_input]) + [gate_name, gate.output]

    return {
        "gate": gate_name,
        "on_max_depth_path": on_path,
        "global_max_depth": global_depth,
        "gate_path_depth": gate_path_depth,
        "example_endpoint": endpoint,
        "example_prefix_path": example_path,
    }


def register_to_register_paths(design: Design, max_paths: int = 200) -> dict:
    """
    List combinational paths from DFF Q pins to downstream DFF D pins.

    The report is capped to keep release-test responses bounded on large
    sequential designs. The total count reflects the paths enumerated before the
    cap is reached.
    """
    rebuild_graph(design)
    paths: list[dict[str, str | list[str]]] = []

    for src_name, src_dff in sorted(design.dffs.items()):
        q = deque([(src_dff.q, [src_dff.q])])
        seen = {src_dff.q}
        while q:
            net, path = q.popleft()
            for sink in design.fanouts.get(net, []):
                kind, name = sink.split(":", 1)
                if kind == "DFF":
                    if name != src_name and design.dffs[name].d == net:
                        paths.append(
                            {
                                "src_dff": src_name,
                                "dst_dff": name,
                                "path": path + [name],
                            }
                        )
                        if len(paths) >= max_paths:
                            return {
                                "paths": paths,
                                "num_paths": len(paths),
                                "truncated": True,
                                "max_paths": max_paths,
                            }
                    continue
                if kind != "GATE":
                    continue
                gate = design.gates[name]
                if gate.output in seen:
                    continue
                seen.add(gate.output)
                q.append((gate.output, path + [gate.name, gate.output]))

    return {
        "paths": paths,
        "num_paths": len(paths),
        "truncated": False,
        "max_paths": max_paths,
    }


def dff_input_logic_structures(design: Design, max_items: int = 200) -> dict:
    """
    Heuristically report DFF D-pin logic that resembles enable/hold structures.

    This is structural rather than semantic. It flags direct AND gating and the
    common Yosys mux decomposition: OR of two AND terms, optionally with one
    select inverted. A hold-like mux is noted when one data term is the DFF Q.
    """
    rebuild_graph(design)
    rows = []
    matched_count = 0
    for name, dff in sorted(design.dffs.items()):
        structures = []
        driver_gate = _gate_driving_net(design, dff.d)
        if driver_gate and driver_gate.type == "and":
            structures.append(
                {
                    "kind": "and_gate",
                    "gate": driver_gate.name,
                    "inputs": list(driver_gate.inputs),
                    "output": driver_gate.output,
                }
            )

        mux_like = _mux_like_structure(design, dff.d, dff.q)
        if mux_like:
            structures.append(mux_like)

        if structures:
            matched_count += 1
            if len(rows) < max_items:
                rows.append(
                    {
                        "name": name,
                        "d": dff.d,
                        "q": dff.q,
                        "clk": dff.clk,
                        "structures": structures,
                    }
                )

    return {
        "num_dffs": len(design.dffs),
        "num_with_structures": matched_count,
        "dffs": rows,
        "max_items": max_items,
        "truncated": matched_count > len(rows),
    }


def dff_relationships(design: Design) -> dict:
    """
    Basic clock-domain and DFF connectivity report.

    This is intentionally structural: it groups DFFs by clock and records
    combinational DFF-to-DFF, PI-to-DFF, and DFF-to-PO relationships without
    crossing through downstream DFFs.
    """
    rebuild_graph(design)
    clock_domains: dict[str, list[str]] = {}
    dffs: dict[str, dict] = {}
    dff_to_dff: list[dict[str, str | list[str]]] = []
    pi_to_dff: list[dict[str, str | list[str]]] = []
    dff_to_po: list[dict[str, str | list[str]]] = []

    for name, dff in sorted(design.dffs.items()):
        clock = dff.clk or "(none)"
        clock_domains.setdefault(clock, []).append(name)
        dffs[name] = {
            "d": dff.d,
            "q": dff.q,
            "clk": dff.clk,
            "rst": dff.rst,
        }

        d_driver = design.drivers.get(dff.d)
        if d_driver:
            dffs[name]["d_driver"] = d_driver

    for source_name, source_dff in sorted(design.dffs.items()):
        endpoints = _combinational_endpoints_from_net(design, source_dff.q)
        for sink_dff in endpoints["dffs"]:
            if sink_dff != source_name:
                dff_to_dff.append({"src_dff": source_name, "dst_dff": sink_dff})
        for output in endpoints["primary_outputs"]:
            dff_to_po.append({"src_dff": source_name, "dst_output": output})

    for pi in sorted(design.inputs):
        endpoints = _combinational_endpoints_from_net(design, pi)
        for sink_dff in endpoints["dffs"]:
            pi_to_dff.append({"src_input": pi, "dst_dff": sink_dff})

    return {
        "num_dffs": len(design.dffs),
        "clock_domains": {clock: sorted(names) for clock, names in sorted(clock_domains.items())},
        "dffs": dffs,
        "dff_to_dff": dff_to_dff,
        "pi_to_dff": pi_to_dff,
        "dff_to_primary_output": dff_to_po,
    }


def _fanin_cone_nets(design: Design, target: str) -> set[str]:
    nets: set[str] = set()
    stack = [target]
    while stack:
        net = stack.pop()
        if net in nets:
            continue
        nets.add(net)
        driver = design.drivers.get(net)
        if not driver or not driver.startswith("GATE:"):
            continue
        gate = design.gates[driver.split(":", 1)[1]]
        stack.extend(gate.inputs)
    return nets


def _gate_driving_net(design: Design, net: str):
    driver = design.drivers.get(net)
    if not driver or not driver.startswith("GATE:"):
        return None
    return design.gates[driver.split(":", 1)[1]]


def _mux_like_structure(design: Design, output_net: str, hold_net: str | None = None) -> dict | None:
    output_gate = _gate_driving_net(design, output_net)
    if output_gate is None or output_gate.type != "or" or len(output_gate.inputs) != 2:
        return None

    term_gates = [_gate_driving_net(design, net) for net in output_gate.inputs]
    if any(gate is None or gate.type != "and" or len(gate.inputs) != 2 for gate in term_gates):
        return None

    assert term_gates[0] is not None and term_gates[1] is not None
    term0 = term_gates[0]
    term1 = term_gates[1]
    term0_inputs = set(term0.inputs)
    term1_inputs = set(term1.inputs)
    select = None
    inverted_select = None
    for net in sorted(term0_inputs | term1_inputs):
        inv_gate = _gate_driving_net(design, net)
        if inv_gate is None or inv_gate.type != "not" or len(inv_gate.inputs) != 1:
            continue
        source = inv_gate.inputs[0]
        if source in term0_inputs or source in term1_inputs:
            select = source
            inverted_select = net
            break

    data_inputs = sorted(
        (term0_inputs | term1_inputs) - {select, inverted_select}
        if select is not None
        else (term0_inputs | term1_inputs)
    )
    return {
        "kind": "mux_like",
        "gate": output_gate.name,
        "terms": [term0.name, term1.name],
        "select": select,
        "inverted_select": inverted_select,
        "data_inputs": data_inputs,
        "hold_like": hold_net in data_inputs if hold_net is not None else False,
        "output": output_net,
    }


def _relax_forward_depths(
    design: Design,
    prefix_depth: dict[str, int],
    prefix_path: dict[str, list[str]],
) -> None:
    for _ in range(max(1, len(design.gates) + 1)):
        changed = False
        for gate in design.gates.values():
            input_depths = [
                prefix_depth.get(input_net, 0 if input_net in {"1'b0", "1'b1", "0", "1"} else None)
                for input_net in gate.inputs
            ]
            known_depths = [depth for depth in input_depths if depth is not None]
            if len(known_depths) != len(gate.inputs):
                continue
            best_depth = max(known_depths, default=0)
            candidate = best_depth + 1
            if candidate <= prefix_depth.get(gate.output, -1):
                continue

            best_input = gate.inputs[input_depths.index(best_depth)]
            prefix_depth[gate.output] = candidate
            prefix_path[gate.output] = prefix_path.get(best_input, [best_input]) + [gate.name, gate.output]
            changed = True
        if not changed:
            return
    raise ValueError("Combinational loop detected while computing global max depth.")


def _relax_reverse_depths(design: Design, suffix_depth: dict[str, int]) -> None:
    for _ in range(max(1, len(design.gates) + 1)):
        changed = False
        for gate in design.gates.values():
            if gate.output not in suffix_depth:
                continue
            candidate = suffix_depth[gate.output] + 1
            for input_net in gate.inputs:
                if candidate > suffix_depth.get(input_net, -1):
                    suffix_depth[input_net] = candidate
                    changed = True
        if not changed:
            return
    raise ValueError("Combinational loop detected while computing reverse depth.")


def _derive_expr(design: Design, net: str, remaining: dict[str, int], visiting: set[str]) -> str:
    if remaining["terms"] <= 0:
        return f"{net} /* truncated */"
    if net in visiting:
        return "1'b0"
    if net in design.inputs or net in {"1'b0", "1'b1", "0", "1"}:
        return net

    driver = design.drivers.get(net)
    if driver is None:
        return net
    if driver.startswith("DFF:"):
        if net in visiting:
            return f"{net} /* sequential loop */"
        dff = design.dffs[driver.split(":", 1)[1]]
        visiting.add(net)
        expression = _derive_expr(design, dff.d, remaining, visiting)
        visiting.remove(net)
        return expression
    if not driver.startswith("GATE:"):
        return net

    visiting.add(net)
    remaining["terms"] -= 1
    gate = design.gates[driver.split(":", 1)[1]]
    args = [_derive_expr(design, item, remaining, visiting) for item in gate.inputs]
    visiting.remove(net)

    if gate.type == "buf" and len(args) == 1:
        return args[0]
    if gate.type == "not" and len(args) == 1:
        return f"!({args[0]})"
    if gate.type == "and":
        return "(" + " & ".join(args) + ")"
    if gate.type == "or":
        return "(" + " | ".join(args) + ")"
    if gate.type == "nand":
        return "!(" + " & ".join(args) + ")"
    if gate.type == "nor":
        return "!(" + " | ".join(args) + ")"
    if gate.type == "xor":
        return "(" + " ^ ".join(args) + ")"
    if gate.type == "xnor":
        return "!(" + " ^ ".join(args) + ")"
    return net


def _combinational_endpoints_from_net(design: Design, source: str) -> dict[str, set[str]]:
    primary_outputs: set[str] = set()
    dffs: set[str] = set()
    stack = [source]
    seen_nets = {source}

    while stack:
        net = stack.pop()
        for sink in design.fanouts.get(net, []):
            if sink.startswith("PO:"):
                primary_outputs.add(sink.split(":", 1)[1])
            elif sink.startswith("DFF:"):
                dffs.add(sink.split(":", 1)[1])
            elif sink.startswith("GATE:"):
                gate = design.gates[sink.split(":", 1)[1]]
                if gate.output not in seen_nets:
                    seen_nets.add(gate.output)
                    stack.append(gate.output)

    return {"primary_outputs": primary_outputs, "dffs": dffs}


def _describe_sink(design: Design, net: str, sink: str) -> dict:
    kind, name = sink.split(":", 1)
    if kind == "GATE":
        gate = design.gates[name]
        input_pins = [index for index, input_net in enumerate(gate.inputs) if input_net == net]
        return {
            "sink": sink,
            "kind": "gate",
            "name": name,
            "gate_type": gate.type,
            "input_pins": input_pins,
            "output": gate.output,
        }
    if kind == "DFF":
        dff = design.dffs[name]
        pins = []
        if dff.d == net:
            pins.append("D")
        if dff.clk == net:
            pins.append("CLK")
        if dff.rst == net:
            pins.append("RST")
        return {"sink": sink, "kind": "dff", "name": name, "pins": pins, "output": dff.q}
    if kind == "PO":
        return {"sink": sink, "kind": "primary_output", "name": name}
    return {"sink": sink, "kind": kind.lower(), "name": name}


def _split_bit_name(net: str) -> tuple[str, int | None]:
    if "[" not in net or not net.endswith("]"):
        return net, None
    base, _, bit_text = net[:-1].partition("[")
    if not bit_text.isdigit():
        return net, None
    return base, int(bit_text)
