from __future__ import annotations

import unittest

from eda.design import DFF, Design, Gate


class DesignTest(unittest.TestCase):
    def test_make_unique_wire_name_avoids_nets_and_instances(self) -> None:
        design = Design(inputs={"n"}, outputs={"y"}, wires={"n_1"})
        design.add_gate(Gate(name="n_2", type="buf", inputs=["n"], output="y"))

        self.assertEqual(design.make_unique_wire_name("n"), "n_3")

    def test_make_unique_gate_name_avoids_gates_and_dffs(self) -> None:
        design = Design()
        design.add_gate(Gate(name="U", type="buf", inputs=["a"], output="b"))
        design.add_dff(DFF(name="U_1", q="q", d="d", clk="clk"))

        self.assertEqual(design.make_unique_gate_name("U"), "U_2")

    def test_unique_names_are_sanitized(self) -> None:
        design = Design()

        self.assertEqual(design.make_unique_wire_name("1 bad-name"), "n_1_bad_name")
        self.assertEqual(design.make_unique_gate_name("bad-name"), "bad_name")


if __name__ == "__main__":
    unittest.main()
