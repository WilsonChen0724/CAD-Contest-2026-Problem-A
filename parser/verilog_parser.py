from __future__ import annotations

import re
from pathlib import Path

from eda.design import Design, DFF, Gate
from eda.graph import rebuild_graph


_PRIMITIVES = {"and", "or", "nand", "nor", "not", "buf", "xor", "xnor"}
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_BIT_SELECT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\[(\d+)\]$")
_CONSTANTS = {"1'b0", "1'b1", "1'bx", "1'bz", "0", "1"}
_DECL_RE = re.compile(r"^(input|output|wire)\s+(.+)$", re.S)
_INST_RE = re.compile(
    r"^(and|or|nand|nor|not|buf|xor|xnor)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$",
    re.S,
)
_DFF_RE = re.compile(
    r"^(dff[A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$",
    re.S | re.I,
)


def parse_verilog(path: str | Path) -> Design:
    """
    MVP primitive parser with expanded bus-bit support.

    Supported Day1/Day2 subset:
        module top(...);
        input a, b;
        input [3:0] bus;
        output y;
        wire n1;
        and U1(n1, a, b);
        buf U2(y, n1);
        dff U3(q, d, clk);

    Bus declarations are expanded in the IR as bit-select nets such as bus[0].
    DFF positional syntax is assumed to be dff <inst>(q, d, clk[, rst]).
    """
    path = Path(path)
    text = path.read_text()
    text = _remove_comments(text)

    module_headers = list(
        re.finditer(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;", text, re.S)
    )
    if not module_headers:
        _raise_parse_error(text, 0, "Missing or invalid module declaration")
    if len(module_headers) > 1:
        _raise_parse_error(text, module_headers[1].start(), "Only one top module is supported")

    module_match = module_headers[0]
    module_name = module_match.group(1)
    endmodule_match = re.search(r"\bendmodule\b", text[module_match.end() :])
    if not endmodule_match:
        _raise_parse_error(text, module_match.start(), f"Module {module_name} is missing endmodule")

    body_start = module_match.end()
    body_end = module_match.end() + endmodule_match.start()
    trailing = text[body_end + len("endmodule") :].strip()
    if trailing:
        _raise_parse_error(text, body_end + len("endmodule"), "Unexpected text after endmodule")

    design = Design(module_name=module_name)

    body = text[body_start:body_end]
    for stmt, absolute_start in _iter_statements(body, body_start, text):
        decl_match = _DECL_RE.match(stmt)
        if decl_match:
            kind, names_text = decl_match.group(1), decl_match.group(2)
            names = _parse_declaration_names(names_text, text, absolute_start)
            target_set = {
                "input": design.inputs,
                "output": design.outputs,
                "wire": design.wires,
            }[kind]
            target_set.update(names)
            continue

        inst_match = _INST_RE.match(stmt)
        if inst_match:
            gate_type, name, pin_text = inst_match.group(1), inst_match.group(2), inst_match.group(3)
            pins = _parse_pin_list(pin_text, text, absolute_start)
            _validate_primitive_pins(gate_type, name, pins, text, absolute_start)
            try:
                design.add_gate(Gate(name=name, type=gate_type, inputs=pins[1:], output=pins[0]))
            except ValueError as exc:
                _raise_parse_error(text, absolute_start, str(exc))
            continue

        dff_match = _DFF_RE.match(stmt)
        if dff_match:
            cell_type, name, pin_text = dff_match.group(1), dff_match.group(2), dff_match.group(3)
            pins = _parse_pin_list(pin_text, text, absolute_start)
            _validate_dff_pins(name, pins, text, absolute_start)
            q, d, clk = pins[0], pins[1], pins[2]
            rst = pins[3] if len(pins) >= 4 else None
            try:
                design.add_dff(DFF(name=name, q=q, d=d, clk=clk, rst=rst, attrs={"cell_type": cell_type}))
            except ValueError as exc:
                _raise_parse_error(text, absolute_start, str(exc))
            continue

        _raise_parse_error(text, absolute_start, f"Unsupported or invalid statement: {stmt}")

    rebuild_graph(design)
    return design


def _remove_comments(text: str) -> str:
    text = re.sub(r"//[^\n\r]*", "", text)

    def keep_newlines(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    text = re.sub(r"/\*.*?\*/", keep_newlines, text, flags=re.S)
    return text


def _iter_statements(body: str, body_start: int, full_text: str) -> list[tuple[str, int]]:
    statements: list[tuple[str, int]] = []
    pos = 0
    for part in body.split(";"):
        absolute_start = body_start + pos
        pos += len(part) + 1
        stmt = part.strip()
        if stmt:
            statements.append((stmt, absolute_start + part.find(stmt)))

    if body.rstrip() and not body.rstrip().endswith(";"):
        _raise_parse_error(full_text, body_start + len(body.rstrip()), "Missing semicolon before endmodule")
    return statements


def _parse_declaration_names(names_text: str, full_text: str, statement_start: int) -> list[str]:
    range_match = re.match(r"^\s*(?:\[(\d+)\s*:\s*(\d+)\]\s*)?(.*)$", names_text, flags=re.S)
    if range_match is None:
        _raise_parse_error(full_text, statement_start, "Invalid declaration")

    msb_text, lsb_text, raw_names = range_match.groups()
    if ("[" in raw_names or "]" in raw_names) and msb_text is None:
        _raise_parse_error(full_text, statement_start, "Bus range must appear before declared names")

    names = [token.strip() for token in raw_names.replace("\n", " ").split(",")]
    if not names or any(not name for name in names):
        _raise_parse_error(full_text, statement_start, "Declaration contains an empty signal name")

    for name in names:
        if not _is_identifier(name):
            _raise_parse_error(full_text, statement_start, f"Invalid signal name: {name}")

    if msb_text is None:
        return names

    msb = int(msb_text)
    lsb = int(lsb_text)
    step = 1 if lsb <= msb else -1
    bit_indexes = range(lsb, msb + step, step)
    return [f"{name}[{index}]" for name in names for index in bit_indexes]


def _parse_pin_list(pin_text: str, full_text: str, statement_start: int) -> list[str]:
    pins = [token.strip() for token in pin_text.replace("\n", " ").split(",")]
    if not pins or any(not pin for pin in pins):
        _raise_parse_error(full_text, statement_start, "Primitive instance contains an empty pin")

    for pin in pins:
        if not _is_signal_or_constant(pin):
            _raise_parse_error(full_text, statement_start, f"Invalid scalar pin: {pin}")
    return pins


def _validate_primitive_pins(
    gate_type: str,
    name: str,
    pins: list[str],
    full_text: str,
    statement_start: int,
) -> None:
    if pins[0] in _CONSTANTS:
        _raise_parse_error(full_text, statement_start, f"Primitive {name} output cannot be a constant")

    if gate_type in {"not", "buf"} and len(pins) != 2:
        _raise_parse_error(
            full_text,
            statement_start,
            f"Primitive {name} ({gate_type}) expects exactly 2 pins: output, input",
        )
    if gate_type not in {"not", "buf"} and len(pins) < 3:
        _raise_parse_error(
            full_text,
            statement_start,
            f"Primitive {name} ({gate_type}) expects at least 3 pins: output, input1, input2",
        )


def _validate_dff_pins(name: str, pins: list[str], full_text: str, statement_start: int) -> None:
    if len(pins) not in {3, 4}:
        _raise_parse_error(
            full_text,
            statement_start,
            f"DFF {name} expects 3 or 4 pins: q, d, clk[, rst]",
        )
    if pins[0] in _CONSTANTS:
        _raise_parse_error(full_text, statement_start, f"DFF {name} q output cannot be a constant")


def _is_identifier(value: str) -> bool:
    return bool(_IDENT_RE.fullmatch(value))


def _is_bit_select(value: str) -> bool:
    return bool(_BIT_SELECT_RE.fullmatch(value))


def _is_signal_or_constant(value: str) -> bool:
    return value in _CONSTANTS or _is_identifier(value) or _is_bit_select(value)


def _raise_parse_error(text: str, index: int, message: str) -> None:
    line = text.count("\n", 0, index) + 1
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index)
    if line_end == -1:
        line_end = len(text)
    source_line = text[line_start:line_end].strip()
    if source_line:
        raise ValueError(f"Verilog parse error at line {line}: {message}. Source: {source_line}")
    raise ValueError(f"Verilog parse error at line {line}: {message}")
