from __future__ import annotations

from eda.design import Design
from eda.graph import rebuild_graph


def check_connectivity(design: Design) -> dict:
    """
    Basic structural check.

    Day1 version:
        Report nets with no driver, ignoring constants.
    """
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
    from eda.analysis import max_depth

    depth, path = max_depth(design, src, dst)
    return {
        "ok": depth <= max_allowed_depth,
        "depth": depth,
        "max_allowed_depth": max_allowed_depth,
        "path": path,
    }
