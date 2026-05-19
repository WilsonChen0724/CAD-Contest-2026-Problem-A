from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.llm_api import LLMAPIError, LLMNotConfiguredError
from agent.llm_planner import plan_with_llm
from agent.plan_checker import (
    PlanValidationError,
    format_plan_error,
    is_unsupported_plan,
    validate_plan,
)
from agent.planner import plan_request
from runtime.config import load_config
from runtime.dispatcher import dispatch_plan
from runtime.response import emit_response
from runtime.state import CurrentState


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-config", dest="config", required=False)
    # planner_mode: rule, llm, hybrid
    # rule: deterministic planner; llm: direct LLM planner; hybrid: rule first, LLM fallback
    parser.add_argument(
        "-planner",
        "--planner",
        choices=("rule", "llm", "hybrid"),
        default="rule",
        help="rule: deterministic planner; llm: direct LLM planner; hybrid: rule first, LLM fallback",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    state = CurrentState()
    prompt = _load_prompt()

    for raw_line in sys.stdin:
        request = raw_line.strip()
        if not request:
            continue

        try:
            plan = _make_plan(request, state, config, prompt, args.planner)
            body = dispatch_plan(state, plan)
        except PlanValidationError as exc:
            body = format_plan_error(exc)
        except LLMNotConfiguredError as exc:
            body = f"LLM planner is not configured: {exc}"
        except LLMAPIError as exc:
            body = f"LLM API error: {exc}"
        except Exception as exc:
            body = f"Error: {exc}"

        print(emit_response(state, body), flush=True)

    return 0


def _make_plan(request: str, state: CurrentState, config: dict, prompt: str, planner_mode: str) -> dict:
    if planner_mode == "rule":
        return validate_plan(plan_request(request, state))

    if planner_mode == "llm":
        return plan_with_llm(prompt, request, config)

    rule_plan = validate_plan(plan_request(request, state))
    if not is_unsupported_plan(rule_plan):
        return rule_plan

    # Hybrid mode keeps the fast deterministic rules, then asks the LLM only
    # when the rule-based planner explicitly cannot map the request.
    return plan_with_llm(prompt, request, config)


def _load_prompt() -> str:
    return (Path(__file__).parent / "agent" / "prompt.txt").read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
