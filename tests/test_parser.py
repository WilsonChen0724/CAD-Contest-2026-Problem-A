from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eda.design import DFF, Design, Gate
from parser.verilog_parser import _yosys_json_to_design, parse_verilog
from parser.verilog_writer import write_verilog


class ParserTest(unittest.TestCase):
    def test_parser_supports_bus_declarations_and_dff(self) -> None:
        source = """
module top(a, clk, y);
input [1:0] a;
input clk;
output y;
wire [1:0] n;
and U0(n[0], a[0], a[1]);
dff FF0(y, n[0], clk);
endmodule
"""
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "bus_dff.v"
            out_path = Path(tmp) / "out.v"
            in_path.write_text(source, encoding="utf-8")

            design = parse_verilog(in_path)
            write_verilog(design, out_path)
            written = out_path.read_text(encoding="utf-8")

        self.assertEqual(design.inputs, {"a[0]", "a[1]", "clk"})
        self.assertIn("n[0]", design.wires)
        self.assertIn("n[1]", design.wires)
        self.assertEqual(design.dffs["FF0"].q, "y")
        self.assertEqual(design.dffs["FF0"].d, "n[0]")
        self.assertEqual(design.dffs["FF0"].clk, "clk")
        self.assertIn("input [1:0] a;", written)
        self.assertIn(
            "dff FF0(.RN(1'b1), .SN(1'b1), .CK(clk), .D(n[0]), .Q(y));",
            written,
        )

    def test_parser_supports_named_pin_dff_from_release_netlists(self) -> None:
        source = """
module top(clk, rst_n, d, y);
input clk, rst_n, d;
output y;
dff g0(.RN(rst_n), .SN(1'b1), .CK(clk), .D(d), .Q(y));
endmodule
"""
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "named_dff.v"
            in_path.write_text(source, encoding="utf-8")

            design = parse_verilog(in_path)

        self.assertEqual(design.dffs["g0"].q, "y")
        self.assertEqual(design.dffs["g0"].d, "d")
        self.assertEqual(design.dffs["g0"].clk, "clk")
        self.assertEqual(design.dffs["g0"].rst, "rst_n")

    def test_parser_and_writer_preserve_independent_dff_set_pin(self) -> None:
        source = """
module top(clk, rst_n, set_n, d, y);
input clk, rst_n, set_n, d;
output y;
dff g0(.RN(rst_n), .SN(set_n), .CK(clk), .D(d), .Q(y));
endmodule
"""
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "named_dff_set.v"
            out_path = Path(tmp) / "out.v"
            in_path.write_text(source, encoding="utf-8")

            design = parse_verilog(in_path)
            write_verilog(design, out_path)
            reparsed = parse_verilog(out_path)

        self.assertEqual(design.dffs["g0"].rst, "rst_n")
        self.assertEqual(design.dffs["g0"].set_signal, "set_n")
        self.assertEqual(reparsed.dffs["g0"].set_signal, "set_n")

    def test_parser_ignores_inactive_named_dff_controls(self) -> None:
        source = """
module top(clk, d, y);
input clk, d;
output y;
dff g0(.RN(1'b1), .SN(1'b1), .CK(clk), .D(d), .Q(y));
endmodule
"""
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "named_dff_no_reset.v"
            in_path.write_text(source, encoding="utf-8")

            design = parse_verilog(in_path)

        self.assertEqual(design.dffs["g0"].q, "y")
        self.assertEqual(design.dffs["g0"].d, "d")
        self.assertEqual(design.dffs["g0"].clk, "clk")
        self.assertIsNone(design.dffs["g0"].rst)

    def test_yosys_fine_cells_are_lowered_to_primitives(self) -> None:
        data = {
            "modules": {
                "top": {
                    "ports": {
                        "a": {"direction": "input", "bits": [1]},
                        "b": {"direction": "input", "bits": [2]},
                        "s": {"direction": "input", "bits": [3]},
                        "y": {"direction": "output", "bits": [4]},
                    },
                    "netnames": {
                        "a": {"bits": [1], "hide_name": 0},
                        "b": {"bits": [2], "hide_name": 0},
                        "s": {"bits": [3], "hide_name": 0},
                        "y": {"bits": [4], "hide_name": 0},
                    },
                    "cells": {
                        "scope": {"type": "$scopeinfo", "connections": {}},
                        "mux$0": {"type": "$_MUX_", "connections": {"A": [1], "B": [2], "S": [3], "Y": [4]}},
                    },
                }
            }
        }

        design = _yosys_json_to_design(data, "top")

        self.assertEqual(len(design.gates), 4)
        self.assertEqual([gate.type for gate in design.gates.values()].count("not"), 1)
        self.assertEqual([gate.type for gate in design.gates.values()].count("and"), 2)
        self.assertEqual([gate.type for gate in design.gates.values()].count("or"), 1)
        self.assertEqual(len(design.dffs), 0)

    def test_yosys_json_preserves_partial_bus_offsets(self) -> None:
        data = {
            "modules": {
                "top": {
                    "ports": {
                        "a": {"direction": "input", "bits": [1]},
                        "y": {"direction": "output", "bits": [101]},
                    },
                    "netnames": {
                        "a": {"bits": [1], "hide_name": 0},
                        "y": {"bits": [101], "hide_name": 0},
                        "n38": {"bits": [100, 101], "offset": 32, "hide_name": 0},
                    },
                    "cells": {
                        "U0": {"type": "$not", "connections": {"A": [1], "Y": [100]}},
                        "U1": {"type": "$_BUF_", "connections": {"A": [100], "Y": [101]}},
                    },
                }
            }
        }

        design = _yosys_json_to_design(data, "top")

        self.assertIn("n38[32]", design.wires)
        self.assertIn("n38[33]", design.wires)
        self.assertNotIn("n38[0]", design.wires)
        self.assertEqual(design.gates["U0"].output, "n38[32]")
        self.assertEqual(design.gates["U1"].inputs, ["n38[32]"])
        self.assertEqual(design.outputs, {"y"})

    def test_yosys_dffe_is_lowered_to_enable_mux_and_dff(self) -> None:
        data = {
            "modules": {
                "top": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [1]},
                        "rst": {"direction": "input", "bits": [2]},
                        "en": {"direction": "input", "bits": [3]},
                        "d": {"direction": "input", "bits": [4]},
                        "q": {"direction": "output", "bits": [5]},
                    },
                    "netnames": {
                        "clk": {"bits": [1], "hide_name": 0},
                        "rst": {"bits": [2], "hide_name": 0},
                        "en": {"bits": [3], "hide_name": 0},
                        "d": {"bits": [4], "hide_name": 0},
                        "q": {"bits": [5], "hide_name": 0},
                    },
                    "cells": {
                        "ff$0": {
                            "type": "$_DFFE_PP0P_",
                            "connections": {"C": [1], "R": [2], "E": [3], "D": [4], "Q": [5]},
                        }
                    },
                }
            }
        }

        design = _yosys_json_to_design(data, "top")
        dff = next(iter(design.dffs.values()))

        self.assertEqual(len(design.gates), 4)
        self.assertEqual(len(design.dffs), 1)
        self.assertEqual(dff.q, "q")
        self.assertEqual(dff.clk, "clk")
        self.assertEqual(dff.rst, "rst")
        self.assertEqual(dff.rst_value, "0")
        self.assertNotEqual(dff.d, "d")

    def test_parser_error_message_includes_source_location(self) -> None:
        source = """
module top(a, y);
input a;
output y;
and U0(y, a, );
endmodule
"""
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "bad.v"
            in_path.write_text(source, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Verilog parse error"):
                parse_verilog(in_path)

    def test_writer_outputs_instances_in_deterministic_order(self) -> None:
        design = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"y"})
        design.add_gate(Gate(name="U2", type="buf", inputs=["b"], output="n2"))
        design.add_gate(Gate(name="U1", type="and", inputs=["a", "n2"], output="n1"))
        design.add_dff(DFF(name="FF0", q="y", d="n1", clk="clk"))

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "ordered.v"
            write_verilog(design, out_path)
            written = out_path.read_text(encoding="utf-8")

        self.assertLess(written.index("and U1"), written.index("buf U2"))
        self.assertLess(written.index("buf U2"), written.index("dff FF0"))

    def test_writer_splits_long_scalar_declarations(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        previous = "a"
        for index in range(40):
            output = "y" if index == 39 else f"n{index}"
            design.add_gate(Gate(name=f"g{index}", type="buf", inputs=[previous], output=output))
            previous = output

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "wrapped.v"
            write_verilog(design, out_path)
            written = out_path.read_text(encoding="utf-8")

        wire_lines = [line for line in written.splitlines() if line.startswith("wire ")]
        self.assertGreater(len(wire_lines), 1)
        self.assertTrue(all(len(line) <= 120 for line in wire_lines))


if __name__ == "__main__":
    unittest.main()
