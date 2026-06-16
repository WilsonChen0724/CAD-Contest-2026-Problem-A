from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eda.design import DFF, Design, Gate
from eda.graph import rebuild_graph
from eda.analysis import max_depth
from eda.transform import (
    balance_depth_with_buffers,
    collapse_back_to_back_inverters,
    constant_propagation,
    insert_dedicated_buffers_for_each_load,
    insert_buffers_for_fanout,
    optimize_cone,
    optimize_design_depth,
    replace_nand_const1_with_not,
    remove_dangling,
    rename_net,
    replace_buffers_with_and,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
    replace_with_and_not,
    replace_xnor_with_nor,
    replace_xor_with_nand,
)
from eda.verify import check_design_equivalence, check_fanout


class TransformTest(unittest.TestCase):
    def test_replace_buffers_rebuilds_graph(self) -> None:
        design = Design(
            inputs={"a", "ctrl"},
            outputs={"y"},
        )
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        rebuild_graph(design)

        result = replace_buffers_with_and(design, ["U0"], "ctrl")

        self.assertEqual(result["num_changed"], 1)
        self.assertIn("GATE:U0", design.fanouts["ctrl"])

    def test_remove_dangling_removes_logic_not_reaching_outputs(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        design.add_gate(Gate(name="U_dead", type="and", inputs=["a", "b"], output="dead"))
        design.add_gate(Gate(name="U_dead2", type="buf", inputs=["dead"], output="dead2"))

        result = remove_dangling(design)

        self.assertEqual(result["removed_gates"], ["U_dead", "U_dead2"])
        self.assertNotIn("U_dead", design.gates)
        self.assertNotIn("dead", design.wires)
        self.assertIn("PO:y", design.fanouts["y"])

    def test_replace_inv_buf_with_inv_collapses_safe_chain(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="y"))

        result = replace_inv_buf_with_inv(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertNotIn("U_inv", design.gates)
        self.assertNotIn("n_mid", design.wires)
        self.assertEqual(design.gates["U_buf"].type, "not")
        self.assertEqual(design.gates["U_buf"].inputs, ["a"])

    def test_replace_inv_buf_with_inv_skips_shared_intermediate_net(self) -> None:
        design = Design(inputs={"a"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="y"))
        design.add_gate(Gate(name="U_other", type="buf", inputs=["n_mid"], output="z"))

        result = replace_inv_buf_with_inv(design)

        self.assertEqual(result["num_changed"], 0)
        self.assertIn("U_inv", design.gates)
        self.assertEqual(design.gates["U_buf"].type, "buf")

    def test_replace_with_and_not_reconstructs_supported_primitives(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y0", "y1", "y2", "y3", "y4"})
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="y0"))
        design.add_gate(Gate(name="U_nand", type="nand", inputs=["a", "b"], output="y1"))
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y2"))
        design.add_gate(Gate(name="U_xor", type="xor", inputs=["a", "b"], output="y3"))
        design.add_gate(Gate(name="U_xnor", type="xnor", inputs=["a", "b"], output="y4"))
        original = deepcopy(design)

        result = replace_with_and_not(design)

        self.assertEqual(result["num_changed"], 5)
        self.assertTrue(all(gate.type in {"and", "not"} for gate in design.gates.values()))
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_xnor_to_nor_avoids_gate_net_name_collisions(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_xnor", type="xnor", inputs=["a", "b"], output="y"))

        replace_xnor_with_nor(design)

        self.assertFalse(set(design.gates) & design.all_nets())
        self.assertEqual({gate.type for gate in design.gates.values()}, {"nor"})

    def test_xor_to_nand_avoids_gate_net_name_collisions(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_xor", type="xor", inputs=["a", "b"], output="y"))

        replace_xor_with_nand(design)

        self.assertFalse(set(design.gates) & design.all_nets())
        self.assertEqual({gate.type for gate in design.gates.values()}, {"nand"})

    def test_collapse_back_to_back_inverters(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n0"], output="y"))
        before = deepcopy(design)

        result = collapse_back_to_back_inverters(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertNotIn("U0", design.gates)
        self.assertEqual(design.gates["U1"].type, "buf")
        self.assertEqual(design.gates["U1"].inputs, ["a"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_replace_or_with_nand_not_rewrites_only_target_cone(self) -> None:
        design = Design(inputs={"a", "b", "c"}, outputs={"flag", "other"})
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="flag"))
        design.add_gate(Gate(name="U_other_or", type="or", inputs=["b", "c"], output="other"))

        result = replace_or_with_nand_not(design, "flag")

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_or"].type, "nand")
        self.assertEqual(len(design.gates["U_or"].inputs), 2)
        added_gates = result["changed"][0]["added_gates"]
        self.assertEqual([design.gates[name].type for name in added_gates], ["not", "not"])
        self.assertEqual(design.gates["U_other_or"].type, "or")

    def test_insert_buffers_for_fanout_builds_equivalent_buffer_tree(self) -> None:
        design = Design(inputs={"src"}, outputs={f"y{i}" for i in range(5)})
        for i in range(5):
            design.add_gate(Gate(name=f"U{i}", type="buf", inputs=["src"], output=f"y{i}"))
        before = deepcopy(design)

        result = insert_buffers_for_fanout(design, "src", 2)

        self.assertGreater(result["num_inserted_buffers"], 0)
        self.assertTrue(check_fanout(design, 2)["ok"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])
        self.assertLessEqual(len(design.fanouts["src"]), 2)

    def test_insert_buffers_for_fanout_rejects_impossible_bound(self) -> None:
        design = Design(inputs={"src"}, outputs={"src", "y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y"))
        rebuild_graph(design)

        with self.assertRaisesRegex(ValueError, "max_fanout >= 2"):
            insert_buffers_for_fanout(design, "src", 1)

    def test_insert_dedicated_buffers_for_each_load(self) -> None:
        design = Design(inputs={"src"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="y1"))
        before = deepcopy(design)

        result = insert_dedicated_buffers_for_each_load(design, "src")

        self.assertEqual(result["num_inserted_buffers"], 2)
        self.assertEqual(len(design.fanouts["src"]), 2)
        self.assertTrue(all(sink.startswith("GATE:") for sink in design.fanouts["src"]))
        self.assertNotEqual(design.gates["U0"].inputs, ["src"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_balance_depth_with_buffers_equalizes_endpoint_depths(self) -> None:
        design = Design(inputs={"src"}, outputs={"y0", "y1", "y2"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y1"))
        design.add_gate(Gate(name="U3", type="buf", inputs=["src"], output="n2"))
        design.add_gate(Gate(name="U4", type="buf", inputs=["n2"], output="n3"))
        design.add_gate(Gate(name="U5", type="buf", inputs=["n3"], output="y2"))
        before = deepcopy(design)

        result = balance_depth_with_buffers(design, "src", ["y0", "y1", "y2"])

        self.assertEqual(result["num_inserted_buffers"], 3)
        self.assertEqual({max_depth(design, "src", dst)[0] for dst in ["y0", "y1", "y2"]}, {3})
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_optimize_cone_removes_buffer_and_double_inverter(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="n_buf"))
        design.add_gate(Gate(name="U_not0", type="not", inputs=["n_buf"], output="n_inv"))
        design.add_gate(Gate(name="U_not1", type="not", inputs=["n_inv"], output="n_clean"))
        design.add_gate(Gate(name="U_and", type="and", inputs=["n_clean", "b"], output="y"))
        before = deepcopy(design)

        result = optimize_cone(design, "y", max_depth=1)

        self.assertEqual(result["final_gate_count"], 1)
        self.assertEqual(result["final_depth"], 1)
        self.assertEqual(set(design.gates), {"U_and"})
        self.assertEqual(design.gates["U_and"].inputs, ["a", "b"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])


    def test_optimize_design_depth_uses_yosys_abc_result(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="and", inputs=["n0", "b"], output="y"))
        rebuild_graph(design)

        optimized = Design(inputs={"a", "b"}, outputs={"y"})
        optimized.add_gate(Gate(name="U_opt", type="and", inputs=["a", "b"], output="y"))
        rebuild_graph(optimized)

        def fake_write_verilog(_design: Design, path: str | Path) -> None:
            Path(path).write_text(
                "module top(a, b, y);\n"
                "input a, b;\n"
                "output y;\n"
                "wire n0;\n"
                "buf U0(n0, a);\n"
                "and U1(y, n0, b);\n"
                "endmodule\n",
                encoding="utf-8",
            )

        def fake_run_yosys_script(_script: str, cwd: Path | None = None):
            out_path = Path(cwd or tempfile.gettempdir()) / "optimized.v"
            out_path.write_text(
                "module top(a, b, y); input a, b; output y; and U_opt(y, a, b); endmodule\n",
                encoding="utf-8",
            )
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("eda.transform.write_verilog", side_effect=fake_write_verilog), patch(
            "eda.transform.run_yosys_script", side_effect=fake_run_yosys_script
        ), patch("eda.transform.parse_verilog", return_value=optimized):
            result = optimize_design_depth(design, max_depth=1)

        self.assertEqual(result["engine"], "yosys_abc")
        self.assertEqual(result["initial_depth"], 2)
        self.assertEqual(result["final_depth"], 1)
        self.assertTrue(result["target_met"])
        self.assertEqual(set(design.gates), {"U_opt"})

    def test_optimize_design_depth_falls_back_when_yosys_fails(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n0"], output="y"))
        rebuild_graph(design)

        with patch("eda.transform.write_verilog", side_effect=RuntimeError("Yosys missing")):
            result = optimize_design_depth(design)

        self.assertEqual(result["engine"], "local_fallback")
        self.assertIn("Yosys missing", result["fallback_reason"])
        self.assertEqual(result["final_depth"], 1)
        self.assertEqual(set(design.gates), {"U1"})
        self.assertEqual(design.gates["U1"].inputs, ["a"])

    def test_optimize_design_depth_skips_yosys_for_fanout_buffered_design(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n__fanout_buf_0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n__fanout_buf_0"], output="y"))
        rebuild_graph(design)

        with patch("eda.transform._optimize_design_depth_with_yosys_abc") as yosys_abc:
            result = optimize_design_depth(design)

        yosys_abc.assert_not_called()
        self.assertEqual(result["engine"], "large_design_bounded_cleanup")
        self.assertIn("Skipped full-design Yosys/ABC", result["fallback_reason"])

    def test_optimize_design_depth_uses_fast_cleanup_for_large_design(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        previous = "a"
        for index in range(10001):
            out = "y" if index == 10000 else f"n{index}"
            design.add_gate(Gate(name=f"U{index}", type="buf", inputs=[previous], output=out))
            previous = out
        rebuild_graph(design)

        with patch("eda.transform._optimize_design_depth_with_yosys_abc") as yosys_abc:
            result = optimize_design_depth(design)

        yosys_abc.assert_not_called()
        self.assertEqual(result["engine"], "large_design_bounded_cleanup")
        self.assertIsNone(result["initial_depth"])
        self.assertIsNone(result["final_depth"])

    def test_optimize_cone_resolves_dff_q_to_d_input_cone(self) -> None:
        design = Design(inputs={"a"}, outputs={"q"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="mid"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["mid"], output="d"))
        design.dffs["FF0"] = DFF(name="FF0", q="q", d="d", clk="clk", rst=None)
        rebuild_graph(design)

        result = optimize_cone(design, "q")

        self.assertEqual(result["resolved_target"], "d")
        self.assertEqual(result["target_resolution"]["kind"], "dff_q_to_d")
        self.assertEqual(result["final_gate_count"], 0)
        self.assertEqual(design.dffs["FF0"].d, "a")

    def test_rename_net_updates_references_and_preserves_function(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n_mid"], output="y"))
        before = deepcopy(design)

        result = rename_net(design, "n_mid", "renamed_mid")

        self.assertEqual(result["old_net"], "n_mid")
        self.assertEqual(result["new_net"], "renamed_mid")
        self.assertNotIn("n_mid", design.wires)
        self.assertIn("renamed_mid", design.wires)
        self.assertEqual(design.gates["U0"].output, "renamed_mid")
        self.assertEqual(design.gates["U1"].inputs, ["renamed_mid"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_rename_net_rejects_existing_net_collision(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="and", inputs=["a", "b"], output="y"))

        with self.assertRaisesRegex(ValueError, "existing net"):
            rename_net(design, "a", "b")

    def test_constant_propagation_simplifies_constants_and_preserves_function(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "1'b1"], output="n_mid"))
        design.add_gate(Gate(name="U_or", type="or", inputs=["n_mid", "1'b0"], output="y"))
        before = deepcopy(design)

        result = constant_propagation(design)

        self.assertGreaterEqual(result["num_changed"], 1)
        self.assertNotIn("U_and", design.gates)
        self.assertNotIn("n_mid", design.wires)
        self.assertEqual(design.gates["U_or"].type, "buf")
        self.assertEqual(design.gates["U_or"].inputs, ["a"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_constant_propagation_rewrites_constant_primary_output_driver(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "1'b0"], output="y"))

        result = constant_propagation(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_and"].type, "buf")
        self.assertEqual(design.gates["U_and"].inputs, ["1'b0"])

    def test_replace_nand_const1_with_not_rewrites_specific_identity(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U_nand1", type="nand", inputs=["a", "1'b1"], output="y"))
        design.add_gate(Gate(name="U_nand0", type="nand", inputs=["b", "1'b0"], output="z"))

        result = replace_nand_const1_with_not(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_nand1"].type, "not")
        self.assertEqual(design.gates["U_nand1"].inputs, ["a"])
        self.assertEqual(design.gates["U_nand0"].type, "nand")


if __name__ == "__main__":
    unittest.main()
