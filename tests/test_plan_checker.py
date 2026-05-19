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


if __name__ == "__main__":
    unittest.main()
