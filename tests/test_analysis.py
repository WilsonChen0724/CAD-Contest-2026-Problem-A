from __future__ import annotations

import unittest

from eda.analysis import max_depth
from eda.design import DFF, Design, Gate


class AnalysisTest(unittest.TestCase):
    def test_max_depth_returns_longest_reconvergent_path(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate(name="U_short", type="buf", inputs=["src"], output="dst"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="dst"))

        depth, path = max_depth(design, "src", "dst")

        self.assertEqual(depth, 2)
        self.assertEqual(path, ["src", "U1", "n1", "U2", "dst"])

    def test_max_depth_does_not_cross_dff_boundary(self) -> None:
        design = Design(module_name="top", inputs={"src", "clk"}, outputs={"dst"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="d"))
        design.add_dff(DFF(name="FF1", d="d", q="q", clk="clk"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["q"], output="dst"))

        depth, path = max_depth(design, "src", "dst")

        self.assertEqual(depth, 0)
        self.assertEqual(path, [])


if __name__ == "__main__":
    unittest.main()
