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
        if node in avoid_set and node != src:
            continue
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
                if nxt in avoid_set:
                    continue
                if gate.name not in seen:
                    seen.add(gate.name)
                    q.append((nxt, path + [gate.name, nxt]))

    return []


def all_paths_pass_through(design: Design, src: str, dst: str, node: str) -> bool:
    """Return True when every combinational path from src to dst crosses node."""
    if src == dst:
        return src == node
    if not find_path(design, src, dst):
        return False
    return not find_path(design, src, dst, avoid=[node])


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
