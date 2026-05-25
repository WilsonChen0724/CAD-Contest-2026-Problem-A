from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eda.design import DFF, Design, Gate
from parser.verilog_parser import parse_verilog
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
        self.assertIn("dff FF0(y, n[0], clk);", written)

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


if __name__ == "__main__":
    unittest.main()
