from __future__ import annotations

from typing import Any, Callable

from agent.llm_api import call_llm
from agent.plan_checker import PlanValidationError, parse_plan_json


LLMCaller = Callable[[str, str, dict], str]
MAX_LLM_RETRIES = 1


def plan_with_llm(
    prompt: str,
    user_request: str,
    config: dict,
    *,
    max_retries: int = MAX_LLM_RETRIES,
    llm_call: LLMCaller = call_llm,
) -> dict[str, Any]:
    """
    Ask the LLM for a tool plan and validate it before dispatch.

    This Day2 wrapper is intentionally separate from llm_api.py:
    - llm_api.py only knows how to call the remote model.
    - plan_checker.py only knows how to validate JSON tool plans.
    - this file owns the retry loop when the model returns invalid JSON or an
      unsafe plan shape.
    """
    request_for_model = user_request
    last_error: PlanValidationError | None = None
    attempts = max_retries + 1

    for attempt in range(attempts):
        raw_plan: str | None = None
        try:
            raw_plan = llm_call(prompt, request_for_model, config)
            return parse_plan_json(raw_plan)
        except PlanValidationError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            if raw_plan is None:
                request_for_model = _build_retry_request_after_missing_tool_call(user_request, exc)
            else:
                request_for_model = _build_repair_request(user_request, raw_plan, exc)

    raise PlanValidationError(
        "LLM planner returned an invalid tool plan after retry. "
        f"Last checker error: {last_error}"
    )


def _build_retry_request_after_missing_tool_call(original_request: str, error: Exception) -> str:
    """Build the retry prompt when the provider timed out or returned no tool call."""
    return (
        "The previous provider call did not return a usable EDA domain tool call.\n"
        "Retry the original request and return exactly one valid domain tool call.\n"
        "Do not include Markdown or explanation.\n\n"
        f"Original user request:\n{original_request}\n\n"
        f"Provider/checker error:\n{error}"
    )


def _build_repair_request(original_request: str, rejected_output: str, error: Exception) -> str:
    """
    Build the retry prompt sent as the next user message.

    The model gets the original user request, the rejected output, and the checker
    error. It is asked to return only corrected JSON so the next response can go
    through the same checker again.
    """
    return (
        "Your previous tool plan was rejected by the JSON/tool checker.\n"
        "Return only one corrected JSON object. Do not include Markdown or explanation.\n\n"
        f"Original user request:\n{original_request}\n\n"
        f"Rejected output:\n{rejected_output}\n\n"
        f"Checker error:\n{error}"
    )
