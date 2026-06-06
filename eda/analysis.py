from __future__ import annotations

from collections import deque
from eda.design import Design
from eda.graph import rebuild_graph

_GATE_COUNT_ORDER = ["and", "or", "not", "nand", "nor", "xor", "xnor", "buf", "dff"]


def find_gates(design: Design, gate_type: str | None = None, name_contains: str | None = None) -> list[str]:
    result = []
    for name, gate in design.gates.items():
        if gate_type is not None and gate.type != gate_type.lower():
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


def io_counts(design: Design) -> dict:
    """Report primary input and primary output counts."""
    return {
        "num_inputs": len(design.inputs),
        "num_outputs": len(design.outputs),
        "inputs": sorted(design.inputs),
        "outputs": sorted(design.outputs),
    }


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
