from __future__ import annotations

from pathlib import Path
from eda.design import Design


def write_verilog(design: Design, path: str | Path) -> None:
    """
    Minimal Verilog writer for MVP scalar netlists.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    ports = list(design.inputs) + list(design.outputs)
    lines = []
    lines.append(f"module {design.module_name}({', '.join(ports)});")
    lines.append("")

    if design.inputs:
        lines.append("input " + ", ".join(sorted(design.inputs)) + ";")
    if design.outputs:
        lines.append("output " + ", ".join(sorted(design.outputs)) + ";")

    internal_wires = sorted(
        net for net in design.wires
        if net not in design.inputs and net not in design.outputs
        and not net.startswith("1'b")
    )
    if internal_wires:
        lines.append("wire " + ", ".join(internal_wires) + ";")

    lines.append("")
    for gate in design.gates.values():
        pins = [gate.output] + gate.inputs
        lines.append(f"{gate.type} {gate.name}({', '.join(pins)});")

    # DFF writing will be added once exact dff syntax is confirmed.
    lines.append("")
    lines.append("endmodule")
    lines.append("")

    p.write_text("\n".join(lines))
