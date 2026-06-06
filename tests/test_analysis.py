from __future__ import annotations

import unittest

from eda.analysis import (
    all_paths_pass_through,
    constant_input_gates,
    dff_relationships,
    fanout_cone,
    gate_on_max_depth_path,
    io_counts,
    max_depth,
    primary_output_cone_sizes,
    register_to_register_paths,
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

    def test_all_paths_pass_through_supports_net_node(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="dst"))

        self.assertTrue(all_paths_pass_through(design, "src", "dst", "n1"))

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

    def test_constant_input_gates(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U0", type="nand", inputs=["a", "1'b1"], output="y"))
        design.add_gate(Gate(name="U1", type="and", inputs=["a", "z"], output="z"))

        report = constant_input_gates(design, gate_type="nand")

        self.assertEqual(report["num_gates"], 1)
        self.assertEqual(report["gates"][0]["name"], "U0")

    def test_primary_output_cone_sizes(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y0"))
        design.add_gate(Gate(name="U3", type="buf", inputs=["b"], output="y1"))

        report = primary_output_cone_sizes(design)

        self.assertEqual(report["y0"]["num_gates"], 2)
        self.assertEqual(report["y1"]["num_gates"], 1)
        self.assertEqual(report["y0"]["gates"], ["U1", "U2"])

    def test_io_counts(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})

        self.assertEqual(io_counts(design)["num_inputs"], 2)
        self.assertEqual(io_counts(design)["num_outputs"], 1)

    def test_gate_on_max_depth_path(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U_short", type="buf", inputs=["b"], output="z"))
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n0"], output="y"))

        self.assertTrue(gate_on_max_depth_path(design, "U0")["on_max_depth_path"])
        self.assertFalse(gate_on_max_depth_path(design, "U_short")["on_max_depth_path"])

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

    def test_register_to_register_paths(self) -> None:
        design = Design(module_name="top", inputs={"a", "clk"}, outputs={"out"})
        design.add_dff(DFF(name="FF0", d="a", q="q0", clk="clk"))
        design.add_gate(Gate(name="U0", type="buf", inputs=["q0"], output="d1"))
        design.add_dff(DFF(name="FF1", d="d1", q="q1", clk="clk"))

        report = register_to_register_paths(design)

        self.assertEqual(report["num_paths"], 1)
        self.assertEqual(report["paths"][0]["src_dff"], "FF0")
        self.assertEqual(report["paths"][0]["dst_dff"], "FF1")


if __name__ == "__main__":
    unittest.main()
