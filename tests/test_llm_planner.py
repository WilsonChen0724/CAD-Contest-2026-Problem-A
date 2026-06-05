from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from agent.llm_api import _extract_anthropic_domain_tool_plan, _extract_domain_tool_plan, _post_json, _resolve_api_key
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

    def test_retries_after_provider_timeout(self) -> None:
        calls: list[str] = []

        def fake_llm(prompt: str, user_request: str, config: dict) -> str:
            del prompt, config
            calls.append(user_request)
            if len(calls) == 1:
                raise PlanValidationError("OpenAI API request timed out before returning a tool call.")
            return '{"op": "read_design", "args": {"path": "testcase/test11/test11.v"}}'

        plan = plan_with_llm("prompt", "Please load test11.v", {}, max_retries=1, llm_call=fake_llm)

        self.assertEqual(plan["op"], "read_design")
        self.assertEqual(len(calls), 2)
        self.assertIn("did not return a usable EDA domain tool call", calls[1])
        self.assertIn("Please load test11.v", calls[1])

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

    def test_extracts_anthropic_domain_tool_call(self) -> None:
        response = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "run_analysis_plan",
                    "input": {
                        "steps": [
                            {
                                "op": "find_path",
                                "args": {"src": "in0", "dst": "out3"},
                                "save_as": None,
                            }
                        ]
                    },
                }
            ]
        }

        plan = _extract_anthropic_domain_tool_plan(response)

        self.assertEqual(
            plan,
            {"steps": [{"op": "find_path", "args": {"src": "in0", "dst": "out3"}}]},
        )

    def test_post_json_retries_transient_http_error(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"{\"ok\": true}"

        calls = []

        def fake_urlopen(request, timeout):
            del request, timeout
            calls.append(1)
            if len(calls) == 1:
                raise __import__("urllib.error").error.HTTPError(
                    "https://example.test",
                    503,
                    "temporary unavailable",
                    hdrs=None,
                    fp=io.BytesIO(b"temporary"),
                )
            return FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = _post_json(
                "https://example.test",
                {"hello": "world"},
                headers={},
                provider="OpenAI",
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_api_key_placeholder_falls_back_to_environment_value(self) -> None:
        self.assertEqual(_resolve_api_key("<YOUR_API_KEY>", "real-key"), "real-key")
        self.assertEqual(_resolve_api_key("config-key", "real-key"), "config-key")
        self.assertIsNone(_resolve_api_key("<YOUR_API_KEY>", None))


if __name__ == "__main__":
    unittest.main()
