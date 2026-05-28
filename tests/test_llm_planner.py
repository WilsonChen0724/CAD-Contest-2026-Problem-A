from __future__ import annotations

import unittest

from agent.llm_api import _extract_domain_tool_plan
from agent.llm_planner import plan_with_llm
from agent.plan_checker import PlanValidationError


class LLMPlannerTest(unittest.TestCase):
    def test_retries_after_invalid_json(self) -> None:
        calls: list[str] = []
        responses = [
            "this is not json",
            '{"op": "find_path", "args": {"src": "in0", "dst": "out3"}}',
        ]

        def fake_llm(prompt: str, user_request: str, config: dict) -> str:
            del prompt, config
            calls.append(user_request)
            return responses[len(calls) - 1]

        plan = plan_with_llm("prompt", "Can in0 reach out3?", {}, max_retries=1, llm_call=fake_llm)

        self.assertEqual(plan["op"], "find_path")
        self.assertEqual(len(calls), 2)
        self.assertIn("previous tool plan was rejected", calls[1])
        self.assertIn("Can in0 reach out3?", calls[1])

    def test_raises_after_retry_is_exhausted(self) -> None:
        def fake_llm(prompt: str, user_request: str, config: dict) -> str:
            del prompt, user_request, config
            return "still not json"

        with self.assertRaises(PlanValidationError) as ctx:
            plan_with_llm("prompt", "bad request", {}, max_retries=1, llm_call=fake_llm)

        self.assertIn("after retry", str(ctx.exception))

    def test_retries_after_tool_call_validation_error(self) -> None:
        calls: list[str] = []

        def fake_llm(prompt: str, user_request: str, config: dict) -> str:
            del prompt, config
            calls.append(user_request)
            if len(calls) == 1:
                raise PlanValidationError("wrong OpenAI domain tool")
            return '{"op": "find_path", "args": {"src": "in0", "dst": "out3"}}'

        plan = plan_with_llm("prompt", "Can in0 reach out3?", {}, max_retries=1, llm_call=fake_llm)

        self.assertEqual(plan["op"], "find_path")
        self.assertEqual(len(calls), 2)
        self.assertIn("wrong OpenAI domain tool", calls[1])

    def test_extracts_openai_domain_tool_call(self) -> None:
        response = {
            "output": [
                {
                    "type": "function_call",
                    "name": "run_analysis_plan",
                    "arguments": (
                        '{"steps":[{"op":"find_path","args":{"src":"in0","dst":"out3"},'
                        '"save_as":null}]}'
                    ),
                }
            ]
        }

        plan = _extract_domain_tool_plan(response)

        self.assertEqual(
            plan,
            {"steps": [{"op": "find_path", "args": {"src": "in0", "dst": "out3"}}]},
        )


if __name__ == "__main__":
    unittest.main()
