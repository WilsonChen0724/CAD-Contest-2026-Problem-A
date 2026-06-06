from __future__ import annotations

import unittest

from agent.tool_schema import TOOL_ALLOWED_OPS, openai_domain_tools


class ToolSchemaTest(unittest.TestCase):
    def test_schema_exposes_backend_analysis_ops(self) -> None:
        analysis_ops = TOOL_ALLOWED_OPS["run_analysis_plan"]

        for op in (
            "report_fanout_cone",
            "report_constant_input_gates",
            "report_io_counts",
            "gate_on_max_depth_path",
            "report_shared_fanin_cone_gates",
            "derive_boolean_equation",
            "report_max_depth_to_dff_d",
            "report_outputs_depth_greater_than",
            "report_last_transform_stats",
        ):
            self.assertIn(op, analysis_ops)

    def test_schema_exposes_backend_transform_ops(self) -> None:
        transform_ops = TOOL_ALLOWED_OPS["run_transform_plan"]

        for op in (
            "replace_nand_const1_with_not",
            "insert_dedicated_buffers_for_each_load",
            "collapse_back_to_back_inverters",
            "insert_buffers_for_all_high_fanout",
            "optimize_design_depth",
            "replace_xnor_nor_with_basic_gates",
            "replace_and_not_with_nand",
            "merge_equivalent_gates",
            "rename_gate",
            "rename_net",
        ):
            self.assertIn(op, transform_ops)

    def test_openai_tool_descriptions_cover_all_ops(self) -> None:
        tools = openai_domain_tools()

        self.assertEqual(len(tools), 4)
        self.assertIn(
            "check_equivalent_to_last_transform_input",
            TOOL_ALLOWED_OPS["run_verify_plan"],
        )


if __name__ == "__main__":
    unittest.main()
