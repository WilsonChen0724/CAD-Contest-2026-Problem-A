from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

from agent.plan_checker import PlanValidationError, validate_domain_tool_plan
from agent.tool_schema import anthropic_domain_tools, openai_domain_tools


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_LLM_REQUEST_TIMEOUT = 20.0
DEFAULT_LLM_MAX_ATTEMPTS = 2


class LLMNotConfiguredError(RuntimeError):
    """Raised when the optional LLM backend is missing required config."""


class LLMAPIError(RuntimeError):
    """Raised when the remote LLM endpoint rejects or fails a request."""


def call_llm(prompt: str, user_request: str, config: dict) -> str:
    """
    Call the configured LLM provider and ask for one EDA domain tool call.

    Supported providers:
    - openai: OpenAI Responses API function tools.
    - anthropic: Anthropic Messages API tool_use blocks.

    Output:
        A JSON string containing the validated plan extracted from the model's
        required domain tool call. The caller still parses it through
        plan_checker.py so the retry path remains centralized.
    """
    settings = _normalize_config(config)
    provider = settings.get("provider", "openai")
    if provider == "openai":
        plan = _call_openai(prompt, user_request, settings)
    elif provider == "anthropic":
        plan = _call_anthropic(prompt, user_request, settings)
    else:
        raise LLMNotConfiguredError(f"Unsupported LLM provider: {provider!r}")
    return json.dumps(plan)


def _call_openai(prompt: str, user_request: str, settings: dict[str, Any]) -> dict[str, Any]:
    api_key = _resolve_api_key(settings.get("openai_api_key"), os.environ.get("OPENAI_API_KEY"))
    if api_key is None:
        raise LLMNotConfiguredError(
            "OpenAI API key is missing. Put it in local config.yaml or set OPENAI_API_KEY."
        )

    payload = {
        "model": settings.get("openai_model") or DEFAULT_OPENAI_MODEL,
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_request},
        ],
        "temperature": _as_float(settings.get("temperature"), default=0.2),
        "max_output_tokens": _as_int(settings.get("max_output_tokens"), default=4096),
        "tools": openai_domain_tools(),
        "tool_choice": "required",
        "parallel_tool_calls": False,
    }

    response_data = _post_json(
        OPENAI_RESPONSES_URL,
        payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        provider="OpenAI",
        max_attempts=_as_int(settings.get("max_attempts"), default=DEFAULT_LLM_MAX_ATTEMPTS),
        request_timeout=_as_float(settings.get("request_timeout"), default=DEFAULT_LLM_REQUEST_TIMEOUT),
    )
    return _extract_openai_domain_tool_plan(response_data)


def _call_anthropic(prompt: str, user_request: str, settings: dict[str, Any]) -> dict[str, Any]:
    api_key = _resolve_api_key(settings.get("anthropic_api_key"), os.environ.get("ANTHROPIC_API_KEY"))
    if api_key is None:
        raise LLMNotConfiguredError(
            "Anthropic API key is missing. Put it in local config.yaml or set ANTHROPIC_API_KEY."
        )

    payload = {
        "model": settings.get("anthropic_model") or DEFAULT_ANTHROPIC_MODEL,
        "system": prompt,
        "messages": [{"role": "user", "content": user_request}],
        "temperature": _as_float(settings.get("temperature"), default=0.2),
        "max_tokens": _as_int(settings.get("max_output_tokens"), default=4096),
        "tools": anthropic_domain_tools(),
        "tool_choice": {"type": "any"},
    }

    response_data = _post_json(
        ANTHROPIC_MESSAGES_URL,
        payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        },
        provider="Anthropic",
        max_attempts=_as_int(settings.get("max_attempts"), default=DEFAULT_LLM_MAX_ATTEMPTS),
        request_timeout=_as_float(settings.get("request_timeout"), default=DEFAULT_LLM_REQUEST_TIMEOUT),
    )
    return _extract_anthropic_domain_tool_plan(response_data)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    provider: str,
    max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS,
    request_timeout: float = DEFAULT_LLM_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            last_error = exc
            if attempt == max_attempts:
                raise PlanValidationError(f"{provider} API request timed out before returning a tool call.") from exc
        except socket.timeout as exc:
            last_error = exc
            if attempt == max_attempts:
                raise PlanValidationError(f"{provider} API request timed out before returning a tool call.") from exc
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == max_attempts:
                raise LLMAPIError(f"{provider} API request failed with HTTP {exc.code}: {message}") from exc
            last_error = LLMAPIError(f"{provider} API transient HTTP {exc.code}: {message}")
        except urllib.error.URLError as exc:
            last_error = exc
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                if attempt == max_attempts:
                    raise PlanValidationError(f"{provider} API request timed out before returning a tool call.") from exc
                continue
            if attempt == max_attempts:
                raise LLMAPIError(f"{provider} API request failed: {exc.reason}") from exc

    raise LLMAPIError(f"{provider} API request failed after retry: {last_error}")

def _normalize_config(config: dict) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    if "raw" in config:
        return _parse_simple_yaml_config(str(config.get("raw", "")))

    openai_config = config.get("openai", {}) or {}
    anthropic_config = config.get("anthropic", {}) or {}
    generation_config = config.get("generation", {}) or {}
    return {
        "provider": config.get("provider", "openai"),
        "openai_api_key": openai_config.get("api_key"),
        "openai_model": openai_config.get("model"),
        "anthropic_api_key": anthropic_config.get("api_key"),
        "anthropic_model": anthropic_config.get("model"),
        "temperature": generation_config.get("temperature"),
        "max_output_tokens": generation_config.get("max_output_tokens"),
    }


def _parse_simple_yaml_config(raw: str) -> dict[str, Any]:
    """Parse the small config.example.yaml shape without adding PyYAML."""
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
        "openai_api_key": result.get("openai.api_key"),
        "openai_model": result.get("openai.model"),
        "anthropic_api_key": result.get("anthropic.api_key"),
        "anthropic_model": result.get("anthropic.model"),
        "temperature": result.get("generation.temperature"),
        "max_output_tokens": result.get("generation.max_output_tokens"),
    }


def _extract_domain_tool_plan(response_data: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for existing tests; extracts OpenAI tool calls."""
    return _extract_openai_domain_tool_plan(response_data)


def _extract_openai_domain_tool_plan(response_data: dict[str, Any]) -> dict[str, Any]:
    calls = [item for item in response_data.get("output", []) if item.get("type") == "function_call"]
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
        _log_domain_tool_call("openai", tool_name, raw_args, None)
        raise PlanValidationError(
            f"OpenAI tool call '{tool_name}' arguments were not valid JSON."
        ) from exc

    return _validate_and_log_tool_call("openai", tool_name, tool_args)


def _extract_anthropic_domain_tool_plan(response_data: dict[str, Any]) -> dict[str, Any]:
    calls = [block for block in response_data.get("content", []) if block.get("type") == "tool_use"]
    if len(calls) != 1:
        raise PlanValidationError(f"Expected exactly one Anthropic domain tool call, got {len(calls)}.")

    call = calls[0]
    tool_name = call.get("name")
    if not isinstance(tool_name, str):
        raise PlanValidationError("Anthropic tool call is missing a string name.")

    tool_args = call.get("input")
    if not isinstance(tool_args, dict):
        raise PlanValidationError(f"Anthropic tool call '{tool_name}' is missing object input.")

    return _validate_and_log_tool_call("anthropic", tool_name, tool_args)


def _validate_and_log_tool_call(provider: str, tool_name: str, arguments: Any) -> dict[str, Any]:
    plan: dict[str, Any] | None = None
    try:
        plan = validate_domain_tool_plan(tool_name, arguments)
        return plan
    finally:
        _log_domain_tool_call(provider, tool_name, arguments, plan)


def _log_domain_tool_call(
    provider: str,
    tool_name: str,
    arguments: Any,
    normalized_plan: dict[str, Any] | None,
) -> None:
    """Emit the raw provider tool call to stderr without touching contest stdout."""
    record = {
        "event": "llm_domain_tool_call",
        "provider": provider,
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
