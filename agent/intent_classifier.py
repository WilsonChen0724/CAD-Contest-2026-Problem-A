from __future__ import annotations

import re
from typing import Any

from agent.plan_checker import PlanValidationError
from agent.tool_schema import ANALYSIS_OPS, IO_OPS, TRANSFORM_OPS, VERIFY_OPS


DESIGN_IO = "design_io"
READ_ONLY_COST_ANALYSIS = "read_only_cost_analysis"
READ_ONLY_VERIFICATION = "read_only_verification"
CONSTRAINT_PRESERVING_TRANSFORM = "constraint_preserving_transform"
EXPLICIT_COST_OPTIMIZATION = "explicit_cost_optimization"
UNSUPPORTED_OR_AMBIGUOUS = "unsupported_or_ambiguous"

_OPTIMIZATION_OPS = {"optimize_cone", "optimize_design_depth"}
_FANOUT_OPTIMIZATION_OPS = {
    "insert_buffers_for_fanout",
    "insert_dedicated_buffers_for_each_load",
    "insert_buffers_for_all_high_fanout",
}


def classify_prompt(text: str) -> str:
    """Conservatively classify a natural-language contest request."""
    low = _normalize(text)
    if not low:
        return UNSUPPORTED_OR_AMBIGUOUS

    if _is_design_io_prompt(low):
        return DESIGN_IO
    if _has_explicit_cost_optimization_intent(low):
        return EXPLICIT_COST_OPTIMIZATION
    if _has_transform_intent(low):
        return CONSTRAINT_PRESERVING_TRANSFORM
    if _has_verification_intent(low):
        return READ_ONLY_VERIFICATION
    if _has_read_only_cost_intent(low):
        return READ_ONLY_COST_ANALYSIS
    return UNSUPPORTED_OR_AMBIGUOUS


def classify_plan(plan: dict[str, Any], prompt_text: str = "") -> str:
    """Classify a validated tool plan by its operation set."""
    ops = _plan_ops(plan)
    if not ops:
        return UNSUPPORTED_OR_AMBIGUOUS
    supported_ops = [op for op in ops if op != "unsupported"]
    if not supported_ops:
        return UNSUPPORTED_OR_AMBIGUOUS

    low_prompt = _normalize(prompt_text)
    if all(op in IO_OPS for op in supported_ops):
        return DESIGN_IO
    if any(op in TRANSFORM_OPS for op in supported_ops):
        if any(op in _OPTIMIZATION_OPS for op in supported_ops):
            return EXPLICIT_COST_OPTIMIZATION
        if _has_explicit_cost_optimization_intent(low_prompt) and any(
            op in _FANOUT_OPTIMIZATION_OPS for op in supported_ops
        ):
            return EXPLICIT_COST_OPTIMIZATION
        return CONSTRAINT_PRESERVING_TRANSFORM
    if all(op in VERIFY_OPS for op in supported_ops):
        return READ_ONLY_VERIFICATION
    if all(op in ANALYSIS_OPS for op in supported_ops):
        return READ_ONLY_COST_ANALYSIS
    return UNSUPPORTED_OR_AMBIGUOUS


def validate_semantic_plan_match(user_request: str, plan: dict[str, Any]) -> None:
    """Raise when a high-confidence prompt/plan category mismatch is detected."""
    expected = classify_prompt(user_request)
    if expected == UNSUPPORTED_OR_AMBIGUOUS:
        return

    actual = classify_plan(plan, user_request)
    reason = _semantic_mismatch_reason(expected, actual, user_request, plan)
    if reason is None:
        return
    raise PlanValidationError(
        "Semantic plan category mismatch. "
        f"Expected: {expected}. Actual: {actual}. Reason: {reason}"
    )


def _semantic_mismatch_reason(
    expected: str,
    actual: str,
    user_request: str,
    plan: dict[str, Any],
) -> str | None:
    if actual == UNSUPPORTED_OR_AMBIGUOUS:
        return "the prompt appears to map to a supported EDA operation, but the plan is unsupported."

    if expected in {READ_ONLY_COST_ANALYSIS, READ_ONLY_VERIFICATION}:
        if actual in {CONSTRAINT_PRESERVING_TRANSFORM, EXPLICIT_COST_OPTIMIZATION}:
            return "the prompt is read-only and should not modify the design."
        return None

    if expected == CONSTRAINT_PRESERVING_TRANSFORM:
        if actual in {READ_ONLY_COST_ANALYSIS, READ_ONLY_VERIFICATION, DESIGN_IO}:
            return "the prompt asks for a design-modifying transformation, but the plan is read-only."
        return None

    if expected == EXPLICIT_COST_OPTIMIZATION:
        ops = set(_plan_ops(plan))
        if actual in {READ_ONLY_COST_ANALYSIS, READ_ONLY_VERIFICATION, DESIGN_IO}:
            return "the prompt asks for an optimization, but the plan is read-only."
        if actual == CONSTRAINT_PRESERVING_TRANSFORM and not _has_equivalent_explicit_cost_op(user_request, ops):
            return "the prompt defines an optimization cost, but the plan lacks an optimization-equivalent operation."
        return None

    if expected == DESIGN_IO and actual != DESIGN_IO:
        return "the prompt is a testcase/design I/O request, but the plan uses a different tool category."

    return None


def _has_equivalent_explicit_cost_op(user_request: str, ops: set[str]) -> bool:
    low = _normalize(user_request)
    if ops & _OPTIMIZATION_OPS:
        return True
    if ("fanout" in low or "load" in low or "buffer" in low) and ops & _FANOUT_OPTIMIZATION_OPS:
        return True
    return False


def _plan_ops(plan: dict[str, Any]) -> list[str]:
    if "steps" in plan and isinstance(plan["steps"], list):
        return [
            str(step.get("op"))
            for step in plan["steps"]
            if isinstance(step, dict) and isinstance(step.get("op"), str)
        ]
    op = plan.get("op")
    return [op] if isinstance(op, str) else []


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _is_design_io_prompt(low: str) -> bool:
    if "testcase" in low and any(word in low for word in ("beginning", "begin", "start", "case name")):
        return True
    if re.search(r"\b(?:load|read)\b", low) and any(
        hint in low for hint in (".v", "verilog", "netlist", "design file")
    ):
        return True
    if re.search(r"\b(?:write|save|dump|emit)\b", low) and ".v" in low:
        return True
    return False


def _has_explicit_cost_optimization_intent(low: str) -> bool:
    patterns = (
        r"\bcost function\b",
        r"\bsmaller is better\b",
        r"\bminimi[sz]e\b",
        r"\breduce (?:the )?critical path\b",
        r"\breduce .*maximum .*depth\b",
        r"\boptimi[sz]e .*depth\b",
        r"\boptimi[sz]e .*logic\b",
    )
    return any(re.search(pattern, low) for pattern in patterns)


def _has_transform_intent(low: str) -> bool:
    if low.startswith(("how many", "what is", "which ", "report ", "list ", "compute ", "determine ")):
        if not any(phrase in low for phrase in ("remove them", "if found", "and collapse", "and update all references")):
            return False
    patterns = (
        r"\btry to (?:replace|convert|restructure|rename|insert|merge|remove)\b",
        r"\b(?:replace|convert|reconstruct|remap|rename|remove|delete|prune|sweep|trim|collapse|simplify|insert|merge|reconnect|decompose)\b",
        r"\bchange the identifier\b",
        r"\bupdate the name\b",
        r"\bupdate .* throughout the netlist\b",
        r"\bremove them if found\b",
    )
    return any(re.search(pattern, low) for pattern in patterns)


def _has_verification_intent(low: str) -> bool:
    patterns = (
        r"\bverify\b",
        r"\bprove\b",
        r"\bconfirm\b",
        r"\bfunctional equivalence\b",
        r"\bequivalent\b",
        r"\balways [01]\b",
        r"\bsymmetric\b",
        r"\bcheck whether\b",
    )
    return any(re.search(pattern, low) for pattern in patterns)


def _has_read_only_cost_intent(low: str) -> bool:
    patterns = (
        r"\bhow many\b",
        r"\bcount\b",
        r"\breport\b",
        r"\bwhat is\b",
        r"\bwhich\b",
        r"\bcompute\b",
        r"\bcalculate\b",
        r"\bdetermine\b",
        r"\blist\b",
        r"\bfind all paths\b",
        r"\bmaximum\b",
        r"\bdepth\b",
        r"\bfanout\b",
        r"\blogic cone\b",
    )
    return any(re.search(pattern, low) for pattern in patterns)
