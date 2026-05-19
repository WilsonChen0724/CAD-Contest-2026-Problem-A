from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
