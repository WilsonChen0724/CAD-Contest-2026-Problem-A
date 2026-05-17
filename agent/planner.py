from __future__ import annotations

import json
import re
from typing import Any


def plan_request(user_request: str, state) -> dict[str, Any]:
    """
    Day1 temporary rule-based planner.

    Day2:
        Replace or supplement this with LLM tool-calling.
    """
    text = user_request.strip()
    low = text.lower()

    # Begin testcase.
    if "beginning" in low and "testcase" in low:
        case = _extract_case_name(text) or "unknown_case"
        return {"op": "begin_testcase", "args": {"case_name": case}}

    # Load file.
    if ("load" in low or "read" in low) and (".v" in low):
        path = _extract_quoted_path(text) or _extract_verilog_path(text)
        return {"op": "read_design", "args": {"path": path}}

    # Write file.
    if ("write" in low or "output" in low) and (".v" in low):
        path = _extract_quoted_path(text) or _extract_verilog_path(text)
        return {"op": "write_design", "args": {"path": path}}

    # Find buffers with pattern.
    if "buffer" in low or "buffers" in low:
        if "find" in low:
            pattern = _extract_quoted_text(text) or "_gc__"
            return {
                "op": "find_gates",
                "args": {"gate_type": "buf", "name_contains": pattern},
                "save_as": "found_buffers",
            }

    # Replace found buffers with AND.
    if "replace" in low and "buffer" in low and "and" in low:
        extra_input = "_gc_ctrl"
        m = re.search(r"(_[A-Za-z0-9_]+)", text)
        if m:
            extra_input = m.group(1)
        return {
            "op": "replace_buffers_with_and",
            "args": {"targets_from": "found_buffers", "extra_input": extra_input},
        }

    # Max depth.
    if "maximum logic depth" in low or "max depth" in low:
        src = _extract_after_keyword(text, "from") or "in0"
        dst = _extract_after_keyword(text, "to") or "out0"
        return {"op": "max_depth", "args": {"src": src, "dst": dst}}

    # Find path.
    if "path" in low and "from" in low and "to" in low:
        src = _extract_after_keyword(text, "from") or "in0"
        dst = _extract_after_keyword(text, "to") or "out0"
        return {"op": "find_path", "args": {"src": src, "dst": dst}}

    return {
        "op": "unsupported",
        "args": {
            "reason": "Could not map request to a supported operation in Day1 rule-based planner."
        },
    }


def _extract_case_name(text: str) -> str | None:
    patterns = [
        r"case name is ['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"testcase ['\"]?([A-Za-z0-9_\-]+)['\"]?",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return m.group(1)
    return None


def _extract_quoted_path(text: str) -> str | None:
    m = re.search(r"['\"]([^'\"]+\.v)['\"]", text)
    return m.group(1) if m else None


def _extract_verilog_path(text: str) -> str | None:
    m = re.search(r"([A-Za-z0-9_./\-]+\.v)", text)
    return m.group(1) if m else None


def _extract_quoted_text(text: str) -> str | None:
    m = re.search(r"['\"]([^'\"]+)['\"]", text)
    return m.group(1) if m else None


def _extract_after_keyword(text: str, keyword: str) -> str | None:
    m = re.search(rf"\b{keyword}\s+(?:input\s+|output\s+)?([A-Za-z_][A-Za-z0-9_\[\]]*)", text, flags=re.I)
    return m.group(1) if m else None
