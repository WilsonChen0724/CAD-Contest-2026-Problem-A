from __future__ import annotations

import unittest

from eda.design import Design, Gate
from eda.verify import check_connectivity


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


if __name__ == "__main__":
    unittest.main()
