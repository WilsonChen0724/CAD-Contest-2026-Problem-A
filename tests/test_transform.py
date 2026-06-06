from __future__ import annotations

from copy import deepcopy
import unittest

from eda.design import Design, Gate
from eda.graph import rebuild_graph
from eda.analysis import max_depth
from eda.transform import (
    balance_depth_with_buffers,
    collapse_back_to_back_inverters,
    constant_propagation,
    insert_dedicated_buffers_for_each_load,
    insert_buffers_for_fanout,
    optimize_cone,
    replace_nand_const1_with_not,
    remove_dangling,
    rename_net,
    replace_buffers_with_and,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
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
