from __future__ import annotations

import unittest

from eda.design import Design, Gate
from eda.verify import (
    _BooleanEngine,
    _abc_output_name,
    _expr_to_abc_design,
    check_connectivity,
    check_design_equivalence,
    check_equivalence,
    check_property,
)


class VerifyTest(unittest.TestCase):
    def test_connectivity_reports_duplicate_gate_drivers(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["a"], output="y"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["b"], output="y"))

        result = check_connectivity(design)

        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicate_drivers"]["y"], ["GATE:U1", "GATE:U2"])

    def test_connectivity_reports_pi_and_gate_duplicate_driver(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"a"})
        design.add_gate(Gate(name="U1", type="buf", inputs=["a"], output="a"))

        result = check_connectivity(design)

        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicate_drivers"]["a"], ["PI:a", "GATE:U1"])

    def test_check_equivalence_accepts_matching_expression(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"z"})
        design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="z"))

        result = check_equivalence(design, "a & b", "z")

        self.assertTrue(result["ok"])

    def test_check_equivalence_returns_counterexample(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"z"})
        design.add_gate(Gate(name="U1", type="or", inputs=["a", "b"], output="z"))

        result = check_equivalence(design, "a & b", "z")

        self.assertFalse(result["ok"])
        self.assertIsInstance(result["counterexample"], dict)

    def test_check_property_accepts_implication(self) -> None:
        design = Design(module_name="top", inputs={"req", "busy"}, outputs={"done"})
        design.add_gate(Gate(name="U_not_busy", type="not", inputs=["busy"], output="n_busy"))
        design.add_gate(Gate(name="U_done", type="and", inputs=["req", "n_busy"], output="done"))

        result = check_property(design, "done", "done -> (req & !busy)")

        self.assertTrue(result["ok"])

    def test_check_property_returns_counterexample(self) -> None:
        design = Design(module_name="top", inputs={"req", "busy"}, outputs={"done"})
        design.add_gate(Gate(name="U_done", type="buf", inputs=["busy"], output="done"))

        result = check_property(design, "done", "done -> (req & !busy)")

        self.assertFalse(result["ok"])
        self.assertIsInstance(result["counterexample"], dict)

    def test_check_property_requires_target_reference(self) -> None:
        design = Design(module_name="top", inputs={"req"}, outputs={"done"})
        design.add_gate(Gate(name="U_done", type="buf", inputs=["req"], output="done"))

        with self.assertRaisesRegex(ValueError, "must reference target"):
            check_property(design, "done", "req")

    def test_check_design_equivalence_accepts_same_output_function(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        before.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        after.add_gate(Gate(name="U_na", type="not", inputs=["a"], output="na"))
        after.add_gate(Gate(name="U_nb", type="not", inputs=["b"], output="nb"))
        after.add_gate(Gate(name="U_nand", type="nand", inputs=["na", "nb"], output="y"))

        result = check_design_equivalence(before, after)

        self.assertTrue(result["ok"])

    def test_check_design_equivalence_reports_changed_output_function(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        before.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        after.add_gate(Gate(name="U_and", type="and", inputs=["a", "b"], output="y"))

        result = check_design_equivalence(before, after)

        self.assertFalse(result["ok"])
        self.assertIn("y", result["failures"])

    def test_abc_expression_emitter_reuses_shared_subtrees(self) -> None:
        design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "b"], output="n1"))
        design.add_gate(Gate(name="U_or", type="or", inputs=["n1", "n1"], output="y"))
        engine = _BooleanEngine()
        expr = engine.net_expr(design, "y")
        output = _abc_output_name(expr.vars())

        emitted = _expr_to_abc_design(expr, output, "expr_test")

        self.assertEqual(len(emitted.gates), 3)


if __name__ == "__main__":
    unittest.main()
