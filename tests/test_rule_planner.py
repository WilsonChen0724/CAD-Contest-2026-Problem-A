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


if __name__ == "__main__":
    unittest.main()
