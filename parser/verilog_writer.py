from __future__ import annotations

import re
from pathlib import Path
from eda.design import Design


def write_verilog(design: Design, path: str | Path) -> None:
    """
    Minimal Verilog writer for MVP scalar netlists.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    ports = _format_port_list(design.inputs, design.outputs)
    lines = []
    lines.append(f"module {design.module_name}({', '.join(ports)});")
    lines.append("")

    lines.extend(_format_declarations("input", design.inputs))
    lines.extend(_format_declarations("output", design.outputs))

    internal_wires = sorted(
        net for net in design.wires
        if net not in design.inputs and net not in design.outputs
        and not net.startswith("1'b")
        and net not in {"0", "1"}
    )
    if internal_wires:
        lines.extend(_format_declarations("wire", set(internal_wires)))

    lines.append("")
    for gate in design.gates.values():
        pins = [gate.output] + gate.inputs
        lines.append(f"{gate.type} {gate.name}({', '.join(pins)});")

    # Parser support assumes positional DFF syntax: dff <inst>(q, d, clk[, rst]).
    # The writer preserves that normalized form for parsed sequential cells.
    for dff in design.dffs.values():
        pins = [dff.q, dff.d]
        if dff.clk:
            pins.append(dff.clk)
        if dff.rst:
            pins.append(dff.rst)
        cell_type = dff.attrs.get("cell_type", "dff")
        lines.append(f"{cell_type} {dff.name}({', '.join(pins)});")

    lines.append("")
    lines.append("endmodule")
    lines.append("")

    p.write_text("\n".join(lines))


def _format_port_list(inputs: set[str], outputs: set[str]) -> list[str]:
    return _base_names(inputs) + _base_names(outputs)


def _format_declarations(kind: str, nets: set[str]) -> list[str]:
    scalars, buses = _split_scalars_and_buses(nets)
    lines: list[str] = []
    if scalars:
        lines.append(f"{kind} " + ", ".join(sorted(scalars)) + ";")
    for base, indexes in sorted(buses.items()):
        if _is_contiguous(indexes):
            lines.append(f"{kind} [{max(indexes)}:{min(indexes)}] {base};")
        else:
            bit_names = [f"{base}[{index}]" for index in sorted(indexes)]
            lines.append(f"{kind} " + ", ".join(bit_names) + ";")
    return lines


def _base_names(nets: set[str]) -> list[str]:
    scalars, buses = _split_scalars_and_buses(nets)
    return sorted(scalars) + sorted(buses)


def _split_scalars_and_buses(nets: set[str]) -> tuple[set[str], dict[str, set[int]]]:
    scalars: set[str] = set()
    buses: dict[str, set[int]] = {}
    for net in nets:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)\[(\d+)\]", net)
        if match:
            buses.setdefault(match.group(1), set()).add(int(match.group(2)))
        else:
            scalars.add(net)
    return scalars, buses


def _is_contiguous(indexes: set[int]) -> bool:
    return indexes == set(range(min(indexes), max(indexes) + 1))
