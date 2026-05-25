from __future__ import annotations

import unittest

from agent.plan_checker import PlanValidationError, format_plan_error, parse_plan_json, validate_plan


class PlanCheckerTest(unittest.TestCase):
    def test_accepts_valid_single_step_plan(self) -> None:
        plan = parse_plan_json('{"op": "find_path", "args": {"src": "in0", "dst": "out3"}}')

        self.assertEqual(plan["op"], "find_path")
        self.assertEqual(plan["args"]["src"], "in0")
        self.assertEqual(plan["args"]["dst"], "out3")

    def test_rejects_unknown_operation(self) -> None:
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({"op": "invent_tool", "args": {}})

        self.assertIn("Unsupported operation", str(ctx.exception))

    def test_formats_checker_error_for_user_response(self) -> None:
        message = format_plan_error(PlanValidationError("Operation 'read_design' is missing path."))

        self.assertIn("Tool call rejected by plan checker", message)
        self.assertIn("docs/tool_spec.md", message)

    def test_rejects_invalid_json(self) -> None:
        with self.assertRaises(PlanValidationError):
            parse_plan_json("not json")

    def test_accepts_unsupported_plan_shape_used_by_rule_planner(self) -> None:
        plan = validate_plan({"op": "unsupported", "args": {"reason": "not mapped"}})

        self.assertEqual(plan["op"], "unsupported")

    def test_accepts_new_analysis_operations(self) -> None:
        plans = [
            {"op": "all_paths_pass_through", "args": {"src": "A", "dst": "B", "node": "C"}},
            {"op": "report_outputs_by_cone_size", "args": {"min_gates": 100}},
            {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF1"}},
            {"op": "remove_dangling", "args": {}},
            {"op": "replace_inv_buf_with_inv", "args": {}},
            {"op": "replace_or_with_nand_not", "args": {"cone_target": "flag"}},
        ]

        for plan in plans:
            with self.subTest(op=plan["op"]):
                self.assertEqual(validate_plan(plan), plan)


if __name__ == "__main__":
    unittest.main()
