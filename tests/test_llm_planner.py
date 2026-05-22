from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
