from __future__ import annotations

from eda.design import Design
from eda.graph import rebuild_graph

# todo
# 1. check_duplicate_drivers
# 2. check_missing_drivers
# 3. check_primary_outputs_driven
# 4. check_gate_pin_count
# 5. check_no_unknown_gate_type
# 6. check_floating_inputs
# 7. check_combinational_loop
# 8. check_unused_wires
# 9. check_dangling_gates

def check_connectivity(design: Design) -> dict:
    """Check whether all design nets have legal drivers."""
    rebuild_graph(design)
    missing_drivers = []
    for net in design.all_nets():
        if net not in design.drivers and net not in design.inputs:
            missing_drivers.append(net)
    return {
        "ok": len(missing_drivers) == 0,
        "missing_drivers": sorted(missing_drivers),
    }


def check_fanout(design: Design, max_fanout: int) -> dict:
    """Report nets whose fanout exceeds the given limit."""
    rebuild_graph(design)
    violations = {
        net: len(sinks)
        for net, sinks in design.fanouts.items()
        if len(sinks) > max_fanout
    }
    return {
        "ok": len(violations) == 0,
        "violations": violations,
    }


def check_depth(design: Design, src: str, dst: str, max_allowed_depth: int) -> dict:
    """Check whether the max logic depth from src to dst is within limit."""
    from eda.analysis import max_depth

    depth, path = max_depth(design, src, dst)
    return {
        "ok": depth <= max_allowed_depth,
        "depth": depth,
        "max_allowed_depth": max_allowed_depth,
        "path": path,
    }
