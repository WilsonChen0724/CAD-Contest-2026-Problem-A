from __future__ import annotations

import unittest

from agent.plan_checker import (
    PlanValidationError,
    format_plan_error,
    parse_plan_json,
    validate_domain_tool_plan,
    validate_plan,
)


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
            {"op": "report_all_paths", "args": {"src": "A", "dst": "B", "max_paths": 20}},
            {"op": "report_outputs_by_cone_size", "args": {"min_gates": 100}},
            {"op": "report_gate_counts", "args": {}},
            {"op": "report_gate_type_count", "args": {"gate_type": "not"}},
            {"op": "report_fanout", "args": {"net": "n1"}},
            {"op": "report_gate_connections", "args": {"gate": "U0"}},
            {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF1"}},
            {"op": "remove_dangling", "args": {}},
            {"op": "replace_inv_buf_with_inv", "args": {}},
            {"op": "replace_or_with_nand_not", "args": {"cone_target": "flag"}},
            {"op": "insert_buffers_for_fanout", "args": {"net": "clk_en", "max_fanout": 8}},
            {"op": "balance_depth_with_buffers", "args": {"src": "A", "dsts": ["B", "C"]}},
            {"op": "optimize_cone", "args": {"target": "h", "max_depth": 5, "minimize_gate_count": True}},
            {"op": "constant_propagation", "args": {}},
            {"op": "rename_net", "args": {"old_net": "n_old", "new_net": "n_new"}},
            {"op": "rename_gate", "args": {"old_name": "g0", "new_name": "renamed_gate"}},
            {"op": "check_equivalent_to_original", "args": {}},
            {"op": "check_equivalence", "args": {"expr": "a & b", "target": "z"}},
            {"op": "check_property", "args": {"target": "done", "property": "done -> req"}},
        ]

        for plan in plans:
            with self.subTest(op=plan["op"]):
                self.assertEqual(validate_plan(plan), plan)

    def test_accepts_null_save_as_in_plain_plan(self) -> None:
        plan = {"op": "logic_cone", "args": {"target": "y"}, "save_as": None}

        self.assertEqual(validate_plan(plan), plan)

    def test_normalizes_optional_null_args(self) -> None:
        plan = validate_plan({"op": "optimize_cone", "args": {"target": "y", "max_depth": None}})

        self.assertEqual(plan, {"op": "optimize_cone", "args": {"target": "y"}})

        domain_plan = validate_domain_tool_plan(
            "run_transform_plan",
            {"steps": [{"op": "optimize_cone", "args": {"target": "y", "max_depth": None}}]},
        )
        self.assertEqual(domain_plan, {"steps": [{"op": "optimize_cone", "args": {"target": "y"}}]})

    def test_rejects_bool_where_integer_is_required(self) -> None:
        with self.assertRaisesRegex(PlanValidationError, "wrong type"):
            validate_plan({"op": "check_fanout", "args": {"max_fanout": True}})

    def test_rejects_empty_required_string_args(self) -> None:
        invalid_plans = [
            {"op": "insert_buffers_for_fanout", "args": {"net": "", "max_fanout": 4}},
            {"op": "optimize_cone", "args": {"target": "   "}},
            {"op": "balance_depth_with_buffers", "args": {"src": "a", "dsts": [""]}},
            {"op": "rename_gate", "args": {"old_name": "g0", "new_name": ""}},
        ]

        for plan in invalid_plans:
            with self.subTest(op=plan["op"]):
                with self.assertRaisesRegex(PlanValidationError, "empty"):
                    validate_plan(plan)

    def test_rejects_invalid_numeric_bounds(self) -> None:
        invalid_plans = [
            {"op": "report_outputs_by_cone_size", "args": {"min_gates": -1}},
            {"op": "check_depth", "args": {"src": "a", "dst": "y", "max_depth": -1}},
            {"op": "insert_buffers_for_fanout", "args": {"net": "n1", "max_fanout": 1}},
        ]

        for plan in invalid_plans:
            with self.subTest(op=plan["op"]):
                with self.assertRaises(PlanValidationError):
                    validate_plan(plan)

    def test_accepts_valid_provider_domain_tool_plan(self) -> None:
        plan = validate_domain_tool_plan(
            "run_analysis_plan",
            {
                "steps": [
                    {
                        "op": "max_depth",
                        "args": {"src": "in0", "dst": "out3"},
                        "save_as": None,
                    }
                ]
            },
        )

        self.assertEqual(
            plan,
            {"steps": [{"op": "max_depth", "args": {"src": "in0", "dst": "out3"}}]},
        )

    def test_rejects_provider_domain_tool_with_wrong_category(self) -> None:
        with self.assertRaises(PlanValidationError) as ctx:
            validate_domain_tool_plan(
                "run_analysis_plan",
                {
                    "steps": [
                        {
                            "op": "remove_dangling",
                            "args": {},
                            "save_as": None,
                        }
                    ]
                },
            )

        self.assertIn("not allowed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
