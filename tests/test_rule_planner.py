from __future__ import annotations

import unittest

from agent.planner import plan_request


class RulePlannerTest(unittest.TestCase):
    def test_maps_all_paths_pass_through(self) -> None:
        plan = plan_request("Does every path from A to B pass through C?", None)

        self.assertEqual(plan, {"op": "all_paths_pass_through", "args": {"src": "A", "dst": "B", "node": "C"}})

    def test_maps_primary_output_cone_size_report(self) -> None:
        plan = plan_request("Report all primary outputs whose logic cone contains more than 100 gates.", None)

        self.assertEqual(plan, {"op": "report_outputs_by_cone_size", "args": {"min_gates": 100}})

    def test_maps_same_clock_domain(self) -> None:
        plan = plan_request("Does FF0 and FF1 under the same clock domain?", None)

        self.assertEqual(plan, {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF1"}})

    def test_maps_remove_dangling(self) -> None:
        plan = plan_request("Remove any dangling gates and nets.", None)

        self.assertEqual(plan, {"op": "remove_dangling", "args": {}})

    def test_maps_replace_inv_buf_with_inv(self) -> None:
        plan = plan_request("Replace all inverters followed by buffers with a single inverter.", None)

        self.assertEqual(plan, {"op": "replace_inv_buf_with_inv", "args": {}})

    def test_maps_replace_or_with_nand_not(self) -> None:
        plan = plan_request("Replace all 2-input OR gates in the cone of flag with NAND and NOT gates.", None)

        self.assertEqual(plan, {"op": "replace_or_with_nand_not", "args": {"cone_target": "flag"}})


if __name__ == "__main__":
    unittest.main()
