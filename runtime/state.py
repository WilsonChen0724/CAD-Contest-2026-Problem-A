from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda.design import Design

@dataclass
class CurrentState:
    """Mutable runtime state for one interactive testcase session."""
    config: dict[str, Any] = field(default_factory=dict)
    testcase: str | None = None
    response_id: int = 1
    design: Design | None = None
    original_design: Design | None = None
    design_path: Path | None = None
    previous_results: dict[str, Any] = field(default_factory = dict)
    last_transform_result: dict[str, Any] | None = None
    last_transform_input: Design | None = None
    log_path: Path | None = None
    output_dir: Path = Path("output")
    log_dir: Path = Path("output/logs")

    def begin_testcase(self, case_name: str) -> None:
        """Start a clean testcase and create its log file."""
        self.testcase = case_name
        self.response_id = 1
        self.design = None
        self.original_design = None
        self.design_path = None
        self.previous_results.clear()
        self.last_transform_result = None
        self.last_transform_input = None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"{case_name}.log"
        self.log_path.write_text("", encoding="utf-8")

    def next_response_id(self) -> int:
        """Return the current response id and advance the counter."""
        rid = self.response_id
        self.response_id += 1
        return rid

    def remember_result(self, name: str, result: Any, kind: str) -> None:
        """Store a named intermediate result for later tool calls."""
        self.previous_results[name] = {
            "kind": kind,
            "value": result,
        }

    def get_result(self, name: str, expected_kind: str | None = None) -> Any:
        """Fetch a named intermediate result and optionally check its kind."""
        if name not in self.previous_results:
            raise KeyError(f'Previous result "{name}" does not exist.')
        stored = self.previous_results[name]
        if not isinstance(stored, dict) or "value" not in stored:
            raise TypeError(f'Previous result "{name}" has invalid format.')
        if expected_kind is not None and stored.get("kind") != expected_kind:
            raise TypeError(
                f'Previous result "{name}" has kind "{stored.get("kind")}", '
                f'expected "{expected_kind}".'
            )
        return stored["value"]
