from __future__ import annotations

import unittest

from eda.analysis import (
    all_paths_pass_through,
    dff_relationships,
    fanout_cone,
    max_depth,
    primary_output_cone_sizes,
)
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

    def test_all_paths_pass_through(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="dst"))

        self.assertTrue(all_paths_pass_through(design, "src", "dst", "U1"))
        self.assertFalse(all_paths_pass_through(design, "src", "dst", "missing"))

        design.add_gate(Gate(name="U_bypass", type="buf", inputs=["src"], output="dst"))
        self.assertFalse(all_paths_pass_through(design, "src", "dst", "U1"))

    def test_fanout_cone_stops_at_dff_boundary(self) -> None:
        design = Design(module_name="top", inputs={"src", "clk"}, outputs={"out"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="out"))
        design.add_dff(DFF(name="FF1", d="n1", q="q", clk="clk"))
        design.add_gate(Gate(name="U_after", type="buf", inputs=["q"], output="after"))

        cone = fanout_cone(design, "src")

        self.assertEqual(cone["gates"], ["U1", "U2"])
        self.assertEqual(cone["primary_outputs"], ["out"])
        self.assertEqual(cone["dff_sinks"], ["FF1"])

    def test_primary_output_cone_sizes(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y0"))
        design.add_gate(Gate(name="U3", type="buf", inputs=["b"], output="y1"))

        report = primary_output_cone_sizes(design)

        self.assertEqual(report["y0"]["num_gates"], 2)
        self.assertEqual(report["y1"]["num_gates"], 1)
        self.assertEqual(report["y0"]["gates"], ["U1", "U2"])

    def test_dff_relationships_basic_clock_domains(self) -> None:
        design = Design(module_name="top", inputs={"a", "clk"}, outputs={"out"})
        design.add_gate(Gate(name="U_in", type="buf", inputs=["a"], output="d0"))
        design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        design.add_gate(Gate(name="U_mid", type="buf", inputs=["q0"], output="d1"))
        design.add_dff(DFF(name="FF1", d="d1", q="q1", clk="clk"))
        design.add_gate(Gate(name="U_out", type="buf", inputs=["q1"], output="out"))

        report = dff_relationships(design)

        self.assertEqual(report["clock_domains"], {"clk": ["FF0", "FF1"]})
        self.assertIn({"src_dff": "FF0", "dst_dff": "FF1"}, report["dff_to_dff"])
        self.assertIn({"src_input": "a", "dst_dff": "FF0"}, report["pi_to_dff"])
        self.assertIn({"src_dff": "FF1", "dst_output": "out"}, report["dff_to_primary_output"])


if __name__ == "__main__":
    unittest.main()
