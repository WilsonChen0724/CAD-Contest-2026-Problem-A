from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from agent.plan_checker import PlanValidationError, validate_domain_tool_plan
from agent.tool_schema import openai_domain_tools


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"


class LLMNotConfiguredError(RuntimeError):
    """Raised when the optional LLM backend is missing required config."""


class LLMAPIError(RuntimeError):
    """Raised when the remote LLM endpoint rejects or fails a request."""


def call_llm(prompt: str, user_request: str, config: dict) -> str:
    """
    Call OpenAI and ask it to return one JSON tool plan.

    Input:
        prompt:
            System/planner instruction text, usually loaded from agent/prompt.txt.
        user_request:
            One natural-language request from stdin.
        config:
            Runtime config. This function accepts either a structured dict like
            {"provider": "openai", "openai": {"api_key": "...", "model": "..."}}
            or the Day1 loader shape {"raw": "...yaml text..."}.

    Output:
        A JSON string containing the validated plan extracted from the model's
        required domain tool call. The caller still parses it through
        plan_checker.py so the retry path remains centralized.
    """
    settings = _normalize_config(config)
    provider = settings.get("provider", "openai")
    if provider != "openai":
        raise LLMNotConfiguredError(f"Unsupported LLM provider for Day2 agent: {provider!r}")

    api_key = _resolve_api_key(settings.get("api_key"), os.environ.get("OPENAI_API_KEY"))
    if api_key is None:
        raise LLMNotConfiguredError(
            "OpenAI API key is missing. Put it in local config.yaml or set OPENAI_API_KEY."
        )

    model = settings.get("model") or DEFAULT_MODEL
    temperature = _as_float(settings.get("temperature"), default=0.2)
    max_output_tokens = _as_int(settings.get("max_output_tokens"), default=4096)

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": prompt,
            },
            {
                "role": "user",
                "content": user_request,
            },
        ],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "tools": openai_domain_tools(),
        "tool_choice": "required",
        "parallel_tool_calls": False,
    }

    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise LLMAPIError(f"OpenAI API request failed with HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise LLMAPIError(f"OpenAI API request failed: {exc.reason}") from exc

    plan = _extract_domain_tool_plan(response_data)
    return json.dumps(plan)


def _normalize_config(config: dict) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    if "raw" in config:
        return _parse_simple_yaml_config(str(config.get("raw", "")))

    openai_config = config.get("openai", {}) or {}
    generation_config = config.get("generation", {}) or {}
    return {
        "provider": config.get("provider", "openai"),
        "api_key": openai_config.get("api_key"),
        "model": openai_config.get("model"),
        "temperature": generation_config.get("temperature"),
        "max_output_tokens": generation_config.get("max_output_tokens"),
    }


def _parse_simple_yaml_config(raw: str) -> dict[str, Any]:
    """
    Parse the small config.example.yaml shape without adding a PyYAML dependency.
    """
    result: dict[str, Any] = {}
    section: str | None = None

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":"):
            section = line[:-1].strip()
            continue
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")

        if section is None:
            result[key] = value
        else:
            result[f"{section}.{key}"] = value

    return {
        "provider": result.get("provider", "openai"),
        "api_key": result.get("openai.api_key"),
        "model": result.get("openai.model"),
        "temperature": result.get("generation.temperature"),
        "max_output_tokens": result.get("generation.max_output_tokens"),
    }


def _extract_domain_tool_plan(response_data: dict[str, Any]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for item in response_data.get("output", []):
        if item.get("type") == "function_call":
            calls.append(item)

    if len(calls) != 1:
        raise PlanValidationError(f"Expected exactly one OpenAI domain tool call, got {len(calls)}.")

    call = calls[0]
    tool_name = call.get("name")
    if not isinstance(tool_name, str):
        raise PlanValidationError("OpenAI tool call is missing a string name.")

    raw_args = call.get("arguments")
    if not isinstance(raw_args, str):
        raise PlanValidationError(f"OpenAI tool call '{tool_name}' is missing JSON arguments.")

    try:
        tool_args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        _log_domain_tool_call(tool_name, raw_args, None)
        raise PlanValidationError(
            f"OpenAI tool call '{tool_name}' arguments were not valid JSON."
        ) from exc

    plan: dict[str, Any] | None = None
    try:
        plan = validate_domain_tool_plan(tool_name, tool_args)
        return plan
    finally:
        _log_domain_tool_call(tool_name, tool_args, plan)


def _log_domain_tool_call(tool_name: str, arguments: Any, normalized_plan: dict[str, Any] | None) -> None:
    """
    Emit the raw OpenAI tool call to stderr without touching contest stdout.

    The release runner captures stderr per testcase, so these records behave
    like a planner trace while keeping #RESPONSE blocks clean.
    """
    record = {
        "event": "openai_domain_tool_call",
        "tool": tool_name,
        "arguments": arguments,
        "normalized_plan": normalized_plan,
    }
    print("[llm-tool-call]", file=sys.stderr)
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)


def _resolve_api_key(config_key: Any, env_key: str | None) -> str | None:
    for candidate in (config_key, env_key):
        if isinstance(candidate, str):
            value = candidate.strip()
            if value and not value.startswith("<"):
                return value
    return None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
