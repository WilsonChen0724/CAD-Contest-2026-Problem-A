from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from eda.design import Design, DFF, Gate
from eda.graph import rebuild_graph
from parser.yosys_tools import quote_yosys_path, run_yosys_script


_PRIMITIVES = {"and", "or", "nand", "nor", "not", "buf", "xor", "xnor"}
_PRIMITIVE_RE = re.compile(
    r"^(and|or|nand|nor|not|buf|xor|xnor)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$",
    re.S,
)
_DFF_RE = re.compile(
    r"^(dff[A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$",
    re.S | re.I,
)
_NAMED_PIN_RE = re.compile(r"^\s*\.([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)\s*$", re.S)
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\(", re.S)
_WRAPPER_PREFIX = "__cada_"


def parse_verilog(path: str | Path) -> Design:
    """
    Parse Verilog through Yosys and convert the Yosys JSON netlist to Design.

    Primitive gate instances are rewritten to private wrapper cells before
    Yosys sees the file. This keeps instance names and buffer cells intact while
    still letting Yosys handle Verilog parsing, port expansion, and syntax
    checking.
    """
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8")
    top_module = _find_top_module(text)
    try:
        rewritten_text, wrappers = _rewrite_primitives_as_wrappers(text)
    except ValueError as exc:
        raise ValueError(f"Verilog parse error in {source_path}: {exc}") from exc
    prelude = _build_wrapper_prelude(wrappers)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        yosys_input = tmp_dir / "input_for_yosys.v"
        yosys_json = tmp_dir / "design.json"
        yosys_prefix = prelude + "\n" if prelude else ""
        yosys_input.write_text(yosys_prefix + rewritten_text, encoding="utf-8")
        line_offset = yosys_prefix.count("\n")

        script = "\n".join(
            [
                f"read_verilog -noopt {quote_yosys_path(yosys_input)}",
                f"hierarchy -check -top {top_module}",
                "proc",
                f"write_json {quote_yosys_path(yosys_json)}",
            ]
        )
        completed = run_yosys_script(script, cwd=source_path.parent)
        if completed.returncode != 0:
            raise ValueError(
                _format_yosys_parse_error(
                    source_path,
                    completed.stdout,
                    completed.stderr,
                    line_offset=line_offset,
                )
            )

        data = json.loads(yosys_json.read_text(encoding="utf-8"))

    return _yosys_json_to_design(data, top_module)


def _find_top_module(text: str) -> str:
    match = _MODULE_RE.search(text)
    if not match:
        raise ValueError("Verilog parse error: missing module declaration")
    return match.group(1)


def _format_yosys_parse_error(source_path: Path, stdout: str, stderr: str, line_offset: int = 0) -> str:
    raw_message = (stderr or stdout or "unknown Yosys error").strip()
    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    location = _extract_yosys_location(raw_message)
    if location is None:
        return f"Verilog parse error in {source_path}: {raw_message}"

    yosys_line_no, column_no = location
    line_no = max(1, yosys_line_no - line_offset)
    source_line = _line_at(source_text, line_no)
    detail = _last_error_line(raw_message)
    pointer = " " * max(column_no - 1, 0) + "^" if column_no is not None else ""
    parts = [
        f"Verilog parse error in {source_path} at line {line_no}"
        + (f", column {column_no}" if column_no is not None else "")
        + f": {detail}",
        f"Source: {source_line.strip()}",
    ]
    if pointer:
        parts.append(f"        {pointer}")
    return "\n".join(parts)


def _extract_yosys_location(message: str) -> tuple[int, int | None] | None:
    line_column_match = re.search(r":(\d+):(\d+):\s*(?:ERROR:\s*)?", message)
    if line_column_match:
        return int(line_column_match.group(1)), int(line_column_match.group(2))

    line_match = re.search(r":(\d+):\s*ERROR:", message)
    if line_match:
        return int(line_match.group(1)), None
    return None


def _line_at(text: str, line_no: int) -> str:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1]
    return ""


def _last_error_line(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    for line in reversed(lines):
        if "ERROR:" in line:
            return line.split("ERROR:", 1)[1].strip()
    return lines[-1] if lines else "unknown Yosys error"


def _rewrite_primitives_as_wrappers(text: str) -> tuple[str, set[tuple[str, int]]]:
    wrappers: set[tuple[str, int]] = set()
    statements: list[str] = []

    for raw_statement in text.split(";"):
        stripped = raw_statement.strip()
        if not stripped:
            statements.append(raw_statement)
            continue

        primitive_match = _PRIMITIVE_RE.match(stripped)
        dff_match = _DFF_RE.match(stripped)
        if primitive_match:
            gate_type, inst_name, pin_text = primitive_match.groups()
            pins = _split_pin_list(pin_text)
            input_count = len(pins) - 1
            if gate_type in {"not", "buf"} and input_count != 1:
                raise ValueError(f"Primitive {inst_name} ({gate_type}) expects one input")
            if gate_type not in {"not", "buf"} and input_count < 2:
                raise ValueError(f"Primitive {inst_name} ({gate_type}) expects at least two inputs")
            wrapper_type = _gate_wrapper_name(gate_type, input_count)
            wrappers.add((gate_type, input_count))
            statements.append(_replace_statement(raw_statement, f"{wrapper_type} {inst_name}({', '.join(pins)})"))
            continue

        if dff_match:
            _, inst_name, pin_text = dff_match.groups()
            pins = _normalize_dff_pins(inst_name, pin_text)
            wrapper_type = _dff_wrapper_name(len(pins))
            wrappers.add(("dff", len(pins)))
            statements.append(_replace_statement(raw_statement, f"{wrapper_type} {inst_name}({', '.join(pins)})"))
            continue

        statements.append(raw_statement)

    return ";".join(statements), wrappers


def _replace_statement(original: str, replacement: str) -> str:
    prefix_len = len(original) - len(original.lstrip())
    suffix_len = len(original) - len(original.rstrip())
    return original[:prefix_len] + replacement + original[len(original) - suffix_len :]


def _split_pin_list(pin_text: str) -> list[str]:
    pins: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(pin_text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Instance pin list has unmatched ')'")
        elif char == "," and depth == 0:
            pins.append(pin_text[start:index].strip())
            start = index + 1
    if depth != 0:
        raise ValueError("Instance pin list has unmatched '('")
    pins.append(pin_text[start:].strip())
    if not pins or any(not pin for pin in pins):
        raise ValueError("Instance contains an empty pin")
    return pins


def _normalize_dff_pins(inst_name: str, pin_text: str) -> list[str]:
    pins = _split_pin_list(pin_text)
    if not _looks_like_named_pins(pins):
        if len(pins) not in {3, 4}:
            raise ValueError(f"DFF {inst_name} expects q, d, clk[, rst]")
        return pins

    named = _parse_named_pin_list(inst_name, pins)
    required = {"Q", "D", "CK"}
    missing = sorted(required - set(named))
    if missing:
        raise ValueError(f"DFF {inst_name} is missing named pin(s): {', '.join(missing)}")

    normalized = [named["Q"], named["D"], named["CK"]]
    reset = _select_dff_reset(named)
    if reset is not None:
        normalized.append(reset)
    return normalized


def _looks_like_named_pins(pins: list[str]) -> bool:
    return any(pin.lstrip().startswith(".") for pin in pins)


def _parse_named_pin_list(inst_name: str, pins: list[str]) -> dict[str, str]:
    named: dict[str, str] = {}
    for pin in pins:
        match = _NAMED_PIN_RE.match(pin)
        if not match:
            raise ValueError(f"DFF {inst_name} mixes named and positional pins")
        name = match.group(1).upper()
        value = match.group(2).strip()
        if not value:
            raise ValueError(f"DFF {inst_name} named pin {name} is empty")
        if name in named:
            raise ValueError(f"DFF {inst_name} has duplicate named pin {name}")
        named[name] = value
    return named


def _select_dff_reset(named: dict[str, str]) -> str | None:
    for pin_name in ("RN", "SN", "RST", "RESET"):
        value = named.get(pin_name)
        if value is not None and not _is_inactive_dff_control(pin_name, value):
            return value
    return None


def _is_inactive_dff_control(pin_name: str, value: str) -> bool:
    normalized = value.replace(" ", "").lower()
    if pin_name in {"RN", "SN"}:
        return normalized in {"1'b1", "1", "1'h1", "1'd1"}
    return normalized in {"1'b0", "0", "1'h0", "1'd0"}


def _build_wrapper_prelude(wrappers: set[tuple[str, int]]) -> str:
    modules = [_build_gate_wrapper(gate_type, input_count) for gate_type, input_count in sorted(wrappers) if gate_type != "dff"]
    modules.extend(_build_dff_wrapper(pin_count) for gate_type, pin_count in sorted(wrappers) if gate_type == "dff")
    return "\n\n".join(modules)


def _build_gate_wrapper(gate_type: str, input_count: int) -> str:
    name = _gate_wrapper_name(gate_type, input_count)
    inputs = [f"A{i}" for i in range(input_count)]
    ports = ["Y"] + inputs
    expr = _gate_expression(gate_type, inputs)
    return (
        f"module {name}({', '.join(ports)});\n"
        "output Y;\n"
        f"input {', '.join(inputs)};\n"
        f"assign Y = {expr};\n"
        "endmodule\n"
    )


def _build_dff_wrapper(pin_count: int) -> str:
    name = _dff_wrapper_name(pin_count)
    if pin_count == 3:
        return (
            f"module {name}(q, d, clk);\n"
            "output q;\n"
            "input d, clk;\n"
            "endmodule\n"
        )
    return (
        f"module {name}(q, d, clk, rst);\n"
        "output q;\n"
        "input d, clk, rst;\n"
        "endmodule\n"
    )


def _gate_expression(gate_type: str, inputs: list[str]) -> str:
    if gate_type == "buf":
        return inputs[0]
    if gate_type == "not":
        return f"~{inputs[0]}"
    operators = {
        "and": "&",
        "or": "|",
        "xor": "^",
        "nand": "&",
        "nor": "|",
        "xnor": "^",
    }
    joined = f" {operators[gate_type]} ".join(inputs)
    if gate_type in {"nand", "nor", "xnor"}:
        return f"~({joined})"
    return joined


def _gate_wrapper_name(gate_type: str, input_count: int) -> str:
    return f"{_WRAPPER_PREFIX}{gate_type}_{input_count}"


def _dff_wrapper_name(pin_count: int) -> str:
    return f"{_WRAPPER_PREFIX}dff_{pin_count}"


def _yosys_json_to_design(data: dict[str, Any], top_module: str) -> Design:
    modules = data.get("modules", {})
    if top_module not in modules:
        raise ValueError(f"Yosys JSON does not contain top module {top_module}")
    module = modules[top_module]
    design = Design(module_name=top_module)
    bit_names = _build_bit_name_map(module)

    for port_name, port in module.get("ports", {}).items():
        bits = port.get("bits", [])
        expanded_bits = _expand_named_bits(port_name, bits)
        target = design.inputs if port.get("direction") == "input" else design.outputs
        for net in expanded_bits:
            target.add(net)
        if port.get("direction") == "output":
            for bit, net in zip(bits, expanded_bits):
                if bit in {"0", "1", "x", "z"}:
                    const_net = _constant_bit_name(bit)
                    design.add_gate(
                        Gate(
                            name=design.make_unique_gate_name(f"const_{net}"),
                            type="buf",
                            inputs=[const_net],
                            output=net,
                        )
                    )

    for net_name, net_info in module.get("netnames", {}).items():
        if net_info.get("hide_name"):
            continue
        design.wires.update(_expand_named_bits(net_name, net_info.get("bits", [])))

    for cell_name, cell in module.get("cells", {}).items():
        cell_type = cell.get("type", "")
        connections = cell.get("connections", {})
        if cell_type.startswith(_WRAPPER_PREFIX) and not cell_type.startswith(_WRAPPER_PREFIX + "dff_"):
            gate_type, input_count = _decode_gate_wrapper(cell_type)
            inputs = [_net_from_connection(connections[f"A{i}"], bit_names) for i in range(input_count)]
            output = _net_from_connection(connections["Y"], bit_names)
            design.add_gate(Gate(name=cell_name, type=gate_type, inputs=inputs, output=output))
        elif cell_type.startswith(_WRAPPER_PREFIX + "dff_"):
            dff = DFF(
                name=cell_name,
                q=_net_from_connection(connections["q"], bit_names),
                d=_net_from_connection(connections["d"], bit_names),
                clk=_net_from_connection(connections["clk"], bit_names),
                rst=_net_from_connection(connections["rst"], bit_names) if "rst" in connections else None,
                attrs={"cell_type": "dff"},
            )
            design.add_dff(dff)
        else:
            _add_yosys_builtin_cell(design, cell_name, cell_type, connections, bit_names)

    rebuild_graph(design)
    return design


def _decode_gate_wrapper(cell_type: str) -> tuple[str, int]:
    rest = cell_type.removeprefix(_WRAPPER_PREFIX)
    gate_type, count_text = rest.rsplit("_", 1)
    if gate_type not in _PRIMITIVES:
        raise ValueError(f"Unsupported wrapped gate type: {gate_type}")
    return gate_type, int(count_text)


def _add_yosys_builtin_cell(
    design: Design,
    cell_name: str,
    cell_type: str,
    connections: dict[str, list[Any]],
    bit_names: dict[Any, str],
) -> None:
    builtin_map = {
        "$and": "and",
        "$or": "or",
        "$nand": "nand",
        "$nor": "nor",
        "$not": "not",
        "$xor": "xor",
        "$xnor": "xnor",
    }
    if cell_type in builtin_map:
        gate_type = builtin_map[cell_type]
        inputs = [_net_from_connection(connections["A"], bit_names)]
        if "B" in connections:
            inputs.append(_net_from_connection(connections["B"], bit_names))
        output = _net_from_connection(connections["Y"], bit_names)
        design.add_gate(Gate(name=_safe_cell_name(cell_name), type=gate_type, inputs=inputs, output=output))
        return
    if cell_type == "$mux":
        base = _safe_cell_name(cell_name)
        input_a = _net_from_connection(connections["A"], bit_names)
        input_b = _net_from_connection(connections["B"], bit_names)
        select = _net_from_connection(connections["S"], bit_names)
        output = _net_from_connection(connections["Y"], bit_names)
        not_select = design.make_unique_wire_name(f"{base}_not_s")
        a_term = design.make_unique_wire_name(f"{base}_a_term")
        b_term = design.make_unique_wire_name(f"{base}_b_term")
        design.add_gate(Gate(name=design.make_unique_gate_name(f"{base}_not_s"), type="not", inputs=[select], output=not_select))
        design.add_gate(Gate(name=design.make_unique_gate_name(f"{base}_a_term"), type="and", inputs=[input_a, not_select], output=a_term))
        design.add_gate(Gate(name=design.make_unique_gate_name(f"{base}_b_term"), type="and", inputs=[input_b, select], output=b_term))
        design.add_gate(Gate(name=design.make_unique_gate_name(f"{base}_or"), type="or", inputs=[a_term, b_term], output=output))
        return
    if cell_type == "$dff":
        design.add_dff(
            DFF(
                name=_safe_cell_name(cell_name),
                q=_net_from_connection(connections["Q"], bit_names),
                d=_net_from_connection(connections["D"], bit_names),
                clk=_net_from_connection(connections["CLK"], bit_names),
            )
        )
        return
    if cell_type == "$adff":
        design.add_dff(
            DFF(
                name=_safe_cell_name(cell_name),
                q=_net_from_connection(connections["Q"], bit_names),
                d=_net_from_connection(connections["D"], bit_names),
                clk=_net_from_connection(connections["CLK"], bit_names),
                rst=_net_from_connection(connections["ARST"], bit_names),
                attrs={"cell_type": "dff"},
            )
        )
        return
    raise ValueError(f"Unsupported Yosys cell type in top module: {cell_type}")


def _build_bit_name_map(module: dict[str, Any]) -> dict[Any, str]:
    bit_names: dict[Any, str] = {"0": "1'b0", "1": "1'b1", "x": "1'bx", "z": "1'bz"}

    # Prefer top-level port names over internal aliases generated by Yosys.
    # ABC output can alias a port bit to names such as \g221.q or \g221.clk;
    # using the port name keeps primary inputs/outputs connected in Design.
    for port_name, port in module.get("ports", {}).items():
        bits = port.get("bits", [])
        for bit, expanded_name in zip(bits, _expand_named_bits(port_name, bits)):
            if bit not in {"0", "1", "x", "z"}:
                bit_names[bit] = expanded_name

    for name, net_info in module.get("netnames", {}).items():
        if net_info.get("hide_name"):
            continue
        for bit, expanded_name in zip(net_info.get("bits", []), _expand_named_bits(name, net_info.get("bits", []))):
            bit_names.setdefault(bit, expanded_name)
    return bit_names


def _expand_named_bits(name: str, bits: list[Any]) -> list[str]:
    if len(bits) <= 1:
        return [name]
    return [f"{name}[{index}]" for index in range(len(bits))]


def _constant_bit_name(bit: Any) -> str:
    return {"0": "1'b0", "1": "1'b1", "x": "1'bx", "z": "1'bz"}[bit]


def _net_from_connection(bits: list[Any], bit_names: dict[Any, str]) -> str:
    if len(bits) != 1:
        raise ValueError(f"Expected scalar connection, got {bits}")
    bit = bits[0]
    if bit in bit_names:
        return bit_names[bit]
    return f"_yosys_bit_{bit}"


def _safe_cell_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if not safe or safe[0].isdigit():
        safe = "U_" + safe
    return safe
