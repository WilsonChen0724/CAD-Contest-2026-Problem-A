from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from parser.verilog_writer import render_verilog, write_verilog
from runtime.state import CurrentState


def snapshot_design(state: CurrentState, response_id: int, label: str) -> str | None:
    """Write a validation snapshot of the current design when ledger mode is on."""
    if not state.validation_enabled or state.design is None:
        return None
    case_name = state.testcase or "unknown_case"
    snapshot_dir = state.validation_dir / case_name / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return _write_snapshot_with_fallback(
        state.design,
        snapshot_dir / f"response_{response_id:03d}_{label}",
    )


def snapshot_last_transform_input(state: CurrentState, response_id: int) -> str | None:
    """Write the design state captured immediately before the previous transform."""
    if not state.validation_enabled or state.last_transform_input is None:
        return None
    case_name = state.testcase or "unknown_case"
    snapshot_dir = state.validation_dir / case_name / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return _write_snapshot_with_fallback(
        state.last_transform_input,
        snapshot_dir / f"response_{response_id:03d}_last_transform_input",
    )


def _write_snapshot_with_fallback(design: Any, stem: Path) -> str:
    path = stem.with_suffix(".v")
    try:
        write_verilog(design, path)
    except Exception as exc:
        try:
            path.write_text(render_verilog(design), encoding="utf-8")
        except Exception as fallback_exc:
            error_path = stem.with_suffix(".error.txt")
            error_path.write_text(
                f"write_verilog failed: {exc}\nrender_verilog failed: {fallback_exc}",
                encoding="utf-8",
            )
            return str(error_path)
        warning_path = stem.with_suffix(".warning.txt")
        warning_path.write_text(
            f"write_verilog validation failed; wrote unvalidated snapshot fallback.\n{exc}",
            encoding="utf-8",
        )
    return str(path)


def append_validation_record(
    state: CurrentState,
    *,
    response_id: int,
    prompt: str,
    plan: dict[str, Any] | None,
    body: str,
    before_snapshot: str | None,
    after_snapshot: str | None,
    last_transform_input_snapshot: str | None = None,
    error: str | None = None,
) -> None:
    """Append one machine-readable response record for external validation."""
    if not state.validation_enabled:
        return
    if state.validation_ledger_path is None:
        case_name = state.testcase or "unknown_case"
        case_dir = state.validation_dir / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        state.validation_ledger_path = case_dir / "ledger.jsonl"
        if not state.validation_ledger_path.exists():
            state.validation_ledger_path.write_text("", encoding="utf-8")

    record = {
        "case": state.testcase,
        "response_id": response_id,
        "prompt": prompt,
        "plan": _json_safe(plan),
        "body": body,
        "before_snapshot": before_snapshot,
        "after_snapshot": after_snapshot,
        "last_transform_input_snapshot": last_transform_input_snapshot,
        "error": error,
        "last_transform": _json_safe(state.last_transform_result),
        "design_path": str(state.design_path) if state.design_path is not None else None,
        "design_summary": state.design.summary() if state.design is not None else None,
    }
    with state.validation_ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return repr(value)
