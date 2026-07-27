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
    driver_lists: dict[str, list[str]] = {}
    fanouts: dict[str, list[str]] = {}

    def add_driver(net: str, driver: str) -> None:
        driver_lists.setdefault(net, []).append(driver)
        drivers.setdefault(net, driver)

    def add_fanout(net: str, sink: str) -> None:
        fanouts.setdefault(net, []).append(sink)

    for net in design.inputs:
        add_driver(net, f"PI:{net}")

    for value in ["1'b0", "1'b1", "0", "1"]:
        drivers[value] = f"CONST:{value}"

    for out in design.outputs:
        add_fanout(out, f"PO:{out}")

    for gate in design.gates.values():
        add_driver(gate.output, f"GATE:{gate.name}")
        for net in gate.inputs:
            add_fanout(net, f"GATE:{gate.name}")

    for dff in design.dffs.values():
        add_driver(dff.q, f"DFF:{dff.name}")
        add_fanout(dff.d, f"DFF:{dff.name}")
        if dff.clk:
            add_fanout(dff.clk, f"DFF:{dff.name}")
        if dff.rst:
            add_fanout(dff.rst, f"DFF:{dff.name}")

    design.drivers = drivers
    design.driver_lists = driver_lists
    design.fanouts = fanouts


def unique_driver(design: Design, net: str) -> str | None:
    """Return the sole structural driver of net, or None when absent/ambiguous."""
    drivers = design.driver_lists.get(net, [])
    return drivers[0] if len(drivers) == 1 else None
