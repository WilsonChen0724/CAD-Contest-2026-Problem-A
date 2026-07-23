from __future__ import annotations

import unittest

from eda.analysis import (
    all_paths,
    all_paths_pass_through,
    count_paths,
    cone_depth,
    constant_input_gates,
    dff_relationships,
    dff_input_logic_structures,
    fanout_cone,
    find_path,
    find_nand_equivalent_pair,
    gate_on_max_depth_path,
    gate_type_count,
    gate_type_count_in_cone,
    derive_boolean_equation,
    io_counts,
    iter_combinational_paths,
    largest_fanin_cone_output,
    max_register_to_register_depth,
    max_depth_to_dff_d,
    max_depth,
    outputs_depth_greater_than,
    primary_outputs_with_widths,
    primary_output_cone_sizes,
    register_to_register_paths,
    shared_fanin_cone_gates,
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
    def test_all_paths_enumerates_bounded_paths(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="dst"))
        design.add_gate(Gate(name="U_bypass", type="buf", inputs=["src"], output="dst"))

        report = all_paths(design, "src", "dst")

        self.assertEqual(report["num_paths"], 2)
        self.assertIn(["src", "U1", "n1", "U2", "dst"], report["paths"])
        self.assertIn(["src", "U_bypass", "dst"], report["paths"])

    def test_count_paths_uses_exact_dag_dynamic_programming(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="n0"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n0"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n0"], output="n2"))
        design.add_gate(Gate(name="U3", type="or", inputs=["n1", "n2"], output="dst"))

        result = count_paths(design, "src", "dst")

        self.assertTrue(result["acyclic"])
        self.assertEqual(result["num_paths"], 2)

    def test_count_paths_stops_at_destination_node(self) -> None:
        design = Design(module_name="top", inputs={"direct"}, outputs={"direct"})

        result = count_paths(design, "direct", "direct")

        self.assertTrue(result["acyclic"])
        self.assertEqual(result["num_paths"], 1)

    def test_iter_combinational_paths_matches_bounded_enumerator(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate("U0", "buf", ["src"], "n0"))
        design.add_gate(Gate("U1", "not", ["n0"], "n1"))
        design.add_gate(Gate("U2", "buf", ["n0"], "n2"))
        design.add_gate(Gate("U3", "or", ["n1", "n2"], "dst"))

        streamed = list(iter_combinational_paths(design, "src", "dst"))
        bounded = all_paths(design, "src", "dst", max_paths=3)

        self.assertEqual(streamed, bounded["paths"])
        self.assertEqual(len(streamed), 2)

    def test_path_queries_accept_bus_base_destinations(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"out[0]", "out[1]"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="out[0]"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="out[1]"))

        path = find_path(design, "src", "out")
        report = all_paths(design, "src", "out", max_paths=4)

        self.assertTrue(path)
        self.assertEqual(report["num_paths"], 2)
        self.assertIn(["src", "U0", "out[0]"], report["paths"])
        self.assertIn(["src", "U1", "out[1]"], report["paths"])

    def test_path_queries_return_quickly_for_missing_destinations(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        previous = "src"
        for index in range(50):
            output = "dst" if index == 49 else f"n{index}"
            design.add_gate(Gate(name=f"U{index}", type="buf", inputs=[previous], output=output))
            previous = output

        self.assertEqual(find_path(design, "src", "missing_bus"), [])
        self.assertEqual(all_paths(design, "src", "missing_bus")["paths"], [])

    def test_gate_type_count_reports_one_type(self) -> None:
        design = Design(module_name="top", inputs={"a", "clk"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="y"))
        design.add_dff(DFF(name="FF0", d="a", q="q", clk="clk"))

        self.assertEqual(gate_type_count(design, "not"), {"gate_type": "not", "count": 1})
        self.assertEqual(gate_type_count(design, "dff"), {"gate_type": "dff", "count": 1})

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

        largest = largest_fanin_cone_output(design)
        self.assertEqual(largest["max_gates"], 2)
        self.assertEqual(largest["outputs"][0]["output"], "y0")

    def test_primary_outputs_with_widths(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"out[0]", "out[1]", "done"})

        report = primary_outputs_with_widths(design)

        self.assertEqual(report["num_outputs"], 2)
        self.assertIn({"name": "done", "width": 1, "range": None, "bits": ["done"]}, report["outputs"])
        self.assertIn(
            {"name": "out", "width": 2, "range": "[1:0]", "bits": ["out[0]", "out[1]"]},
            report["outputs"],
        )

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
        depth = max_register_to_register_depth(design)

        self.assertEqual(report["num_paths"], 1)
        self.assertEqual(report["paths"][0]["src_dff"], "FF0")
        self.assertEqual(report["paths"][0]["dst_dff"], "FF1")
        self.assertEqual(depth["max_depth"], 1)
        self.assertEqual(depth["src_dff"], "FF0")
        self.assertEqual(depth["dst_dff"], "FF1")

    def test_shared_fanin_cone_gates(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U_shared", type="and", inputs=["a", "b"], output="n0"))
        design.add_gate(Gate(name="U0", type="buf", inputs=["n0"], output="y0"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n0"], output="y1"))

        report = shared_fanin_cone_gates(design, "y0", "y1")

        self.assertEqual(report["shared_gates"], ["U_shared"])

    def test_derive_boolean_equation(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="nand", inputs=["a", "b"], output="y"))

        report = derive_boolean_equation(design, "y")

        self.assertEqual(report["format"], "dag")
        self.assertFalse(report["truncated"])
        self.assertEqual(report["expression"], "y")
        self.assertEqual(
            report["equations"],
            [{"net": "y", "gate": "U0", "type": "nand", "expr": "!(a & b)"}],
        )

    def test_derive_boolean_equation_expands_dff_q_to_d_input(self) -> None:
        design = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="and", inputs=["a", "b"], output="d0"))
        design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        design.add_gate(Gate(name="U1", type="not", inputs=["q0"], output="y"))

        report = derive_boolean_equation(design, "y")

        self.assertEqual(report["expression"], "y")
        self.assertEqual(
            report["equations"],
            [
                {"net": "d0", "gate": "U0", "type": "and", "expr": "(a & b)"},
                {"net": "y", "gate": "U1", "type": "not", "expr": "!(d0)"},
            ],
        )

    def test_derive_boolean_equation_records_sequential_feedback_default(self) -> None:
        design = Design(module_name="top", inputs={"a", "clk"}, outputs={"y"})
        design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        design.add_gate(Gate(name="U0", type="and", inputs=["q0", "a"], output="d0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["q0"], output="y"))

        report = derive_boolean_equation(design, "y")

        self.assertFalse(report["truncated"])
        self.assertEqual(report["sequential_feedback_defaults"], [{"net": "q0", "value": "1'b0"}])
        self.assertIn({"net": "d0", "gate": "U0", "type": "and", "expr": "(1'b0 & a)"}, report["equations"])

    def test_gate_type_count_and_nand_equivalent_pair(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="nand", inputs=["a", "b"], output="y"))

        count = gate_type_count_in_cone(design, "y", "nand")
        pair = find_nand_equivalent_pair(design, "y")

        self.assertEqual(count["num_gates"], 1)
        self.assertTrue(pair["found"])
        self.assertEqual((pair["a"], pair["b"]), ("a", "b"))

    def test_max_depth_to_dff_d_and_output_depth_threshold(self) -> None:
        design = Design(module_name="top", inputs={"a", "clk"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n0"], output="d0"))
        design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["q0"], output="y0"))
        design.add_gate(Gate(name="U3", type="buf", inputs=["a"], output="y1"))

        self.assertEqual(max_depth_to_dff_d(design)["max_depth"], 2)
        self.assertEqual(outputs_depth_greater_than(design, 0)["num_outputs"], 2)
        self.assertEqual(cone_depth(design, "d0")["max_depth"], 2)

    def test_dff_input_logic_structures_detects_and_and_mux_like(self) -> None:
        design = Design(module_name="top", inputs={"a", "en", "clk"}, outputs={"q0", "q1"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "en"], output="d0"))
        design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        design.add_gate(Gate(name="U_not_en", type="not", inputs=["en"], output="n_en"))
        design.add_gate(Gate(name="U_hold", type="and", inputs=["q1", "n_en"], output="hold_term"))
        design.add_gate(Gate(name="U_next", type="and", inputs=["a", "en"], output="next_term"))
        design.add_gate(Gate(name="U_mux", type="or", inputs=["hold_term", "next_term"], output="d1"))
        design.add_dff(DFF(name="FF1", d="d1", q="q1", clk="clk"))

        report = dff_input_logic_structures(design)

        self.assertEqual(report["num_with_structures"], 2)
        ff1 = next(item for item in report["dffs"] if item["name"] == "FF1")
        self.assertTrue(ff1["structures"][0]["hold_like"])


if __name__ == "__main__":
    unittest.main()
