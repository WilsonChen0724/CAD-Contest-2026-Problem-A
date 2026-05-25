from __future__ import annotations

import unittest

from eda.design import Design, Gate
from eda.graph import rebuild_graph
from eda.transform import (
    remove_dangling,
    replace_buffers_with_and,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
)


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


if __name__ == "__main__":
    unittest.main()
