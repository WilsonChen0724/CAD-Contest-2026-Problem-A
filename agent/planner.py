from __future__ import annotations

import re
from typing import Any


SUPPORTED_OPS = {
    "begin_testcase",
    "read_design",
    "write_design",
    "find_path",
    "max_depth",
    "logic_cone",
    "find_gates",
    "replace_buffers_with_and",
    "check_connectivity",
    "check_fanout",
    "check_depth",
}

_SIGNAL_RE = r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?"


def plan_request(user_request: str, state) -> dict[str, Any]:
    """
    Convert one natural-language request into a Tool API plan.

    Args:
        user_request:
            One command read from stdin, for example
            "Find all the buffers which name include '_gc__'".
        state:
            Runtime state passed by main.py. The Day1/Day2 rule planner does not
            need it yet, but the argument is kept for future context-aware rules.

    Returns:
        A dict that matches docs/tool_spec.md, such as
        {"op": "read_design", "args": {"path": "..."}}, or an unsupported plan
        when no deterministic rule can map the request.

    Routing order:
        1. testcase initialization
        2. Verilog read/write
        3. netlist transformations
        4. analysis queries
        5. verification checks
    """
    del state  # Reserved for future context-aware planning.

    text = user_request.strip()
    low = text.lower()

    if not text:
        return _unsupported("Empty request.")

    testcase_plan = _plan_begin_testcase(text, low)
    if testcase_plan:
        return testcase_plan

    io_plan = _plan_design_io(text, low)
    if io_plan:
        return io_plan

    transform_plan = _plan_transform(text, low)
    if transform_plan:
        return transform_plan

    analysis_plan = _plan_analysis(text, low)
    if analysis_plan:
        return analysis_plan

    verify_plan = _plan_verification(text, low)
    if verify_plan:
        return verify_plan

    return _unsupported("Could not map request to a supported operation in the Day1 planner.")


def _plan_begin_testcase(text: str, low: str) -> dict[str, Any] | None:
    """
    Route requests that start a new testcase.

    Args:
        text:
            Original request text. Case and punctuation are preserved so the
            testcase name can be extracted exactly as written.
        low:
            Lowercase request text for keyword matching, such as "beginning",
            "new", "start", and "testcase".

    Returns:
        A begin_testcase plan if the request starts a testcase. Otherwise None,
        which lets plan_request try the next route.
    """
    if "testcase" not in low and "test case" not in low:
        return None
    if not any(word in low for word in ("begin", "beginning", "new", "start", "initialize", "initialise")):
        return None

    case_name = _extract_case_name(text) or "unknown_case"
    return {"op": "begin_testcase", "args": {"case_name": case_name}}


def _plan_design_io(text: str, low: str) -> dict[str, Any] | None:
    """
    Route Verilog file input/output requests.

    Args:
        text:
            Original request text. The path extractor uses this to preserve file
            paths such as 'tests/design/netlist/test8.v'.
        low:
            Lowercase request text for intent keywords. Read intents include
            load/read/parse/open/import. Write intents include
            write/output/save/dump/export.

    Returns:
        read_design or write_design when a .v path and matching intent are
        found. Returns an unsupported plan if a .v path is mentioned but cannot
        be extracted. Returns None when this is not a file IO request.
    """
    if ".v" not in low:
        return None

    path = _extract_quoted_path(text) or _extract_verilog_path(text)
    if path is None:
        return _unsupported("A Verilog file path was mentioned but no .v path could be extracted.")

    if any(word in low for word in ("write", "output", "save", "dump", "export")):
        return {"op": "write_design", "args": {"path": path}}

    if any(word in low for word in ("load", "read", "parse", "open", "import")):
        return {"op": "read_design", "args": {"path": path}}

    return None


def _plan_transform(text: str, low: str) -> dict[str, Any] | None:
    """
    Route netlist transformation requests.

    Args:
        text:
            Original request text. This is used to extract explicit instance
            names and the extra input signal, such as _gc_ctrl.
        low:
            Lowercase request text. The supported Day2 transform rule is
            replace + buffer(s) + AND.

    Returns:
        A replace_buffers_with_and plan. The args contain extra_input plus
        either explicit targets or targets_from="found_buffers". Returns None
        when no supported transformation rule matches.
    """
    if "replace" in low and ("buffer" in low or "buffers" in low) and _mentions_gate_type(low, "and"):
        extra_input = _extract_extra_input(text) or "_gc_ctrl"
        targets = _extract_instance_list(text)

        args: dict[str, Any] = {"extra_input": extra_input}
        if targets and "found" not in low:
            args["targets"] = targets
        else:
            args["targets_from"] = "found_buffers"

        return {"op": "replace_buffers_with_and", "args": args}

    return None


def _plan_analysis(text: str, low: str) -> dict[str, Any] | None:
    """
    Route analysis and query requests.

    Args:
        text:
            Original request text. This preserves signal names, gate patterns,
            quoted strings, and endpoint names such as in0/out3.
        low:
            Lowercase request text for matching analysis phrases like "find",
            "logic cone", "max depth", and "path".

    Returns:
        One of the supported analysis plans: find_gates, logic_cone, max_depth,
        or find_path. Returns None when this is not an analysis request.
    """
    if ("buffer" in low or "buffers" in low or "gate" in low or "gates" in low) and "find" in low:
        gate_type = _extract_gate_type(low)
        name_contains = _extract_name_pattern(text)
        plan: dict[str, Any] = {
            "op": "find_gates",
            "args": {"gate_type": gate_type, "name_contains": name_contains},
        }
        if gate_type == "buf" or "buffer" in low or "buffers" in low:
            plan["save_as"] = "found_buffers"
        return plan

    if "logic cone" in low or "fanin cone" in low or "fan-in cone" in low:
        target = _extract_after_keyword(text, "of") or _extract_after_keyword(text, "for")
        target = target or _extract_after_keyword(text, "target")
        if target:
            return {"op": "logic_cone", "args": {"target": target}}

    if "maximum logic depth" in low or "max logic depth" in low or "max depth" in low:
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            return {"op": "max_depth", "args": {"src": src, "dst": dst}}

    if "path" in low and "from" in low and "to" in low:
        endpoints = _extract_src_dst(text)
        if endpoints:
            src, dst = endpoints
            avoid = _extract_avoid_list(text)
            args: dict[str, Any] = {"src": src, "dst": dst}
            if avoid:
                args["avoid"] = avoid
            return {"op": "find_path", "args": args}

    return None


def _plan_verification(text: str, low: str) -> dict[str, Any] | None:
    """
    Route verification and checking requests.

    Args:
        text:
            Original request text. This is used to extract numeric limits and
            endpoint signals, for example "from in0 to out3 <= 5".
        low:
            Lowercase request text. This route only handles requests containing
            "check" or "verify".

    Returns:
        check_connectivity, check_fanout, or check_depth. Returns an unsupported
        plan when fanout is requested without a numeric limit. Returns None when
        this is not a verification request.
    """
    if "check" not in low and "verify" not in low:
        return None

    if "connectivity" in low or "connection" in low or "floating" in low or "driver" in low:
        return {"op": "check_connectivity", "args": {}}

    if "fanout" in low or "fan-out" in low:
        max_fanout = _extract_limit_int(text)
        if max_fanout is None:
            return _unsupported("Fanout check needs a numeric max_fanout bound.")
        return {"op": "check_fanout", "args": {"max_fanout": max_fanout}}

    if "depth" in low:
        endpoints = _extract_src_dst(text)
        max_depth = _extract_limit_int(text)
        if endpoints and max_depth is not None:
            src, dst = endpoints
            return {
                "op": "check_depth",
                "args": {"src": src, "dst": dst, "max_depth": max_depth},
            }

    return None


def _unsupported(reason: str) -> dict[str, Any]:
    return {"op": "unsupported", "args": {"reason": reason}}


def _extract_case_name(text: str) -> str | None:
    patterns = [
        r"case\s+name\s+is\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"test\s*case\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"testcase\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_quoted_path(text: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+\.v)['\"]", text, flags=re.I)
    return match.group(1) if match else None


def _extract_verilog_path(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9_./\\:\-]+\.v)", text, flags=re.I)
    return match.group(1) if match else None


def _extract_name_pattern(text: str) -> str | None:
    quoted = _extract_quoted_text(text)
    if quoted:
        return quoted

    patterns = [
        r"(?:include|includes|including|contains?|like|named?)\s+([A-Za-z0-9_$*?_\-]+)",
        r"name\s+.*?\b([A-Za-z0-9_$*?_\-]*__[A-Za-z0-9_$*?_\-]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).replace("*", "")

    if "_gc__" in text:
        return "_gc__"
    return None


def _extract_quoted_text(text: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def _extract_gate_type(low: str) -> str | None:
    aliases = {
        "buffer": "buf",
        "buffers": "buf",
        "buf": "buf",
        "and": "and",
        "or": "or",
        "nand": "nand",
        "nor": "nor",
        "not": "not",
        "inv": "not",
        "inverter": "not",
        "xor": "xor",
        "xnor": "xnor",
    }
    for word, gate_type in aliases.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            return gate_type
    return None


def _mentions_gate_type(low: str, gate_type: str) -> bool:
    return re.search(rf"\b{re.escape(gate_type)}\b", low) is not None


def _extract_extra_input(text: str) -> str | None:
    patterns = [
        rf"(?:other|second|extra)\s+input\s+(?:to|as|is)?\s*({_SIGNAL_RE})",
        rf"connect\s+(?:the\s+)?(?:other|second|extra)?\s*input\s+to\s+({_SIGNAL_RE})",
        rf"\bwith\s+({_SIGNAL_RE})\s+as\s+(?:the\s+)?(?:other|second|extra)\s+input",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_instance_list(text: str) -> list[str]:
    quoted = _extract_quoted_text(text)
    if quoted and "," in quoted:
        return [_clean_token(token) for token in quoted.split(",") if _clean_token(token)]

    patterns = [
        r"(?:instances?|gates?|buffers?)\s+([A-Za-z0-9_$,\s]+)\s+(?:with|to|by|using)",
        r"(?:targets?)\s+([A-Za-z0-9_$,\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        raw = match.group(1)
        names = [_clean_token(token) for token in re.split(r"[,\s]+", raw)]
        return [name for name in names if name and name.lower() not in {"found", "the"}]

    return []


def _extract_src_dst(text: str) -> tuple[str, str] | None:
    src = _extract_after_keyword(text, "from")
    dst = _extract_after_keyword(text, "to")
    if src and dst:
        return src, dst
    return None


def _extract_after_keyword(text: str, keyword: str) -> str | None:
    pattern = rf"\b{re.escape(keyword)}\s+(?:input\s+|output\s+|signal\s+|net\s+)?({_SIGNAL_RE})"
    match = re.search(pattern, text, flags=re.I)
    return match.group(1) if match else None


def _extract_avoid_list(text: str) -> list[str]:
    match = re.search(r"\bavoid(?:ing)?\s+([A-Za-z0-9_$,\s]+)", text, flags=re.I)
    if not match:
        return []
    return [
        token
        for token in (_clean_token(part) for part in re.split(r"[,\s]+", match.group(1)))
        if token and token.lower() not in {"and", "or"}
    ]


def _extract_int_after(text: str, keyword: str) -> int | None:
    match = re.search(rf"\b{re.escape(keyword)}\b\D*(\d+)", text, flags=re.I)
    return int(match.group(1)) if match else None


def _extract_limit_int(text: str) -> int | None:
    patterns = [
        r"(?:<=|<|=)\s*(\d+)",
        r"\b(?:max(?:imum)?|limit|bound|threshold|at\s+most|no\s+more\s+than|less\s+than|under)\D+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))
    return _extract_last_int(text)


def _extract_first_int(text: str) -> int | None:
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else None


def _extract_last_int(text: str) -> int | None:
    matches = re.findall(r"\b(\d+)\b", text)
    return int(matches[-1]) if matches else None


def _clean_token(token: str) -> str:
    return token.strip().strip(".,;:()[]{}'\"")
