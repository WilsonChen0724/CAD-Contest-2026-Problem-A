from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


ALLOWED_GATE_TYPES = {
    "and", "or", "nand", "nor", "not", "buf", "xor", "xnor"
}


@dataclass
class Gate:
    """
    Primitive gate instance.

    Positional Verilog convention:
        <type> <name>(<output>, <input1>, <input2>, ...);
    """
    name: str
    type: str
    inputs: list[str]
    output: str
    attrs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = self.type.lower()
        if self.type not in ALLOWED_GATE_TYPES:
            raise ValueError(f"Unsupported gate type: {self.type}")
        if not self.name:
            raise ValueError("Gate name cannot be empty")
        if not self.output:
            raise ValueError(f"Gate {self.name} output cannot be empty")


@dataclass
class DFF:
    """
    Sequential cell.

    The exact pin convention may be extended after reading official testcases.
    For MVP, DFF is treated as a sequential boundary.
    """
    name: str
    d: str
    q: str
    clk: Optional[str] = None
    rst: Optional[str] = None
    rst_value: Optional[str] = None
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Design:
    module_name: str = "top"
    inputs: set[str] = field(default_factory=set)
    outputs: set[str] = field(default_factory=set)
    wires: set[str] = field(default_factory=set)
    gates: dict[str, Gate] = field(default_factory=dict)
    dffs: dict[str, DFF] = field(default_factory=dict)

    # Rebuilt by eda.graph.rebuild_graph()
    drivers: dict[str, str] = field(default_factory=dict)
    fanouts: dict[str, list[str]] = field(default_factory=dict)

    def add_gate(self, gate: Gate) -> None:
        if gate.name in self.gates or gate.name in self.dffs:
            raise ValueError(f"Duplicate instance name: {gate.name}")
        self.gates[gate.name] = gate
        self.wires.add(gate.output)
        for net in gate.inputs:
            if not is_constant(net):
                self.wires.add(net)

    def add_dff(self, dff: DFF) -> None:
        if dff.name in self.gates or dff.name in self.dffs:
            raise ValueError(f"Duplicate instance name: {dff.name}")
        self.dffs[dff.name] = dff
        if not is_constant(dff.d):
            self.wires.add(dff.d)
        self.wires.add(dff.q)
        if dff.clk:
            self.wires.add(dff.clk)
        if dff.rst:
            self.wires.add(dff.rst)

    def all_nets(self) -> set[str]:
        return set(self.inputs) | set(self.outputs) | set(self.wires)

    def summary(self) -> str:
        return (
            f"module={self.module_name}, "
            f"inputs={len(self.inputs)}, outputs={len(self.outputs)}, "
            f"wires={len(self.wires)}, gates={len(self.gates)}, dffs={len(self.dffs)}"
        )


def is_constant(net: str) -> bool:
    return net in {"1'b0", "1'b1", "1'bx", "1'bz", "0", "1"}
