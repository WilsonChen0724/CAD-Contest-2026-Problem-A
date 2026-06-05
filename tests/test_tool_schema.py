from __future__ import annotations

import unittest

from agent.tool_schema import OP_ARG_SCHEMAS, openai_domain_tools


class ToolSchemaTest(unittest.TestCase):
    def test_find_path_schema_uses_spec_avoid_argument(self) -> None:
        schema = OP_ARG_SCHEMAS["find_path"]

        self.assertIn("avoid", schema["properties"])
        self.assertNotIn("avoid_nodes", schema["properties"])
        self.assertFalse(schema["additionalProperties"])

    def test_openai_analysis_tool_uses_per_operation_step_variants(self) -> None:
        tools = {tool["name"]: tool for tool in openai_domain_tools()}
        item_schema = tools["run_analysis_plan"]["parameters"]["properties"]["steps"]["items"]

        find_path_variants = [
            variant
            for variant in item_schema["anyOf"]
            if variant["properties"]["op"]["enum"] == ["find_path"]
        ]

        self.assertEqual(len(find_path_variants), 1)
        args_schema = find_path_variants[0]["properties"]["args"]
        self.assertIn("avoid", args_schema["properties"])
        self.assertNotIn("avoid_nodes", args_schema["properties"])
        self.assertFalse(args_schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
