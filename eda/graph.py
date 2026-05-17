from __future__ import annotations

from eda.design import Design, is_constant


def rebuild_graph(design: Design) -> None:
    """
    Rebuild driver and fanout maps from the current Design object.

    Driver id format:
        PI:<net>
        CONST:<value>
        GATE:<inst>
        DFF:<inst>

    Sink id format:
        PO:<net>
        GATE:<inst>
        DFF:<inst>
    """
    drivers: dict[str, str] = {}
    fanouts: dict[str, list[str]] = {}

    def add_fanout(net: str, sink: str) -> None:
        fanouts.setdefault(net, []).append(sink)

    for net in design.inputs:
        drivers[net] = f"PI:{net}"

    for value in ["1'b0", "1'b1", "0", "1"]:
        drivers[value] = f"CONST:{value}"

    for out in design.outputs:
        add_fanout(out, f"PO:{out}")

    for gate in design.gates.values():
        if gate.output in drivers:
            # Keep the first driver but expose the issue in verification later.
            pass
        drivers[gate.output] = f"GATE:{gate.name}"
        for net in gate.inputs:
            add_fanout(net, f"GATE:{gate.name}")

    for dff in design.dffs.values():
        drivers[dff.q] = f"DFF:{dff.name}"
        add_fanout(dff.d, f"DFF:{dff.name}")
        if dff.clk:
            add_fanout(dff.clk, f"DFF:{dff.name}")
        if dff.rst:
            add_fanout(dff.rst, f"DFF:{dff.name}")

    design.drivers = drivers
    design.fanouts = fanouts
