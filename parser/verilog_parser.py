from __future__ import annotations

import re
from pathlib import Path

from eda.design import Design, Gate
from eda.graph import rebuild_graph


_PRIMITIVES = {"and", "or", "nand", "nor", "not", "buf", "xor", "xnor"}


def parse_verilog(path: str | Path) -> Design:
    """
    MVP scalar primitive parser.

    Supported Day1/Day2 subset:
        module top(...);
        input a, b;
        output y;
        wire n1;
        and U1(n1, a, b);
        buf U2(y, n1);

    This intentionally avoids full Verilog grammar.
    """
    text = Path(path).read_text()
    text = _remove_comments(text)

    module_match = re.search(r"\bmodule\s+(\w+)\s*\(", text)
    module_name = module_match.group(1) if module_match else "top"
    design = Design(module_name=module_name)

    for kind, target_set in [
        ("input", design.inputs),
        ("output", design.outputs),
        ("wire", design.wires),
    ]:
        for decl in re.finditer(rf"\b{kind}\b\s+([^;]+);", text):
            names = _split_names(decl.group(1))
            target_set.update(names)

    inst_re = re.compile(r"\b(and|or|nand|nor|not|buf|xor|xnor)\s+(\w+)\s*\(([^;]+)\)\s*;")
    for m in inst_re.finditer(text):
        gate_type, name, pin_text = m.group(1), m.group(2), m.group(3)
        pins = [p.strip() for p in pin_text.split(",") if p.strip()]
        if len(pins) < 2:
            raise ValueError(f"Primitive {name} has too few pins")
        output = pins[0]
        inputs = pins[1:]
        design.add_gate(Gate(name=name, type=gate_type, inputs=inputs, output=output))

    rebuild_graph(design)
    return design


def _remove_comments(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


def _split_names(text: str) -> list[str]:
    # Day1 MVP: strips simple bus range but stores scalar names only.
    text = re.sub(r"\[[^\]]+\]", " ", text)
    return [
        token.strip()
        for token in text.replace("\n", " ").split(",")
        if token.strip()
    ]
