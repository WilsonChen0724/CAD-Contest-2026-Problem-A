from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda.design import Design


@dataclass
class CurrentState:
    testcase: str | None = None
    response_id: int = 1
    design: Design | None = None
    previous_results: dict[str, Any] = field(default_factory=dict)
    log_path: Path | None = None
    output_dir: Path = Path("output")

    def begin_testcase(self, case_name: str) -> None:
        self.testcase = case_name
        self.design = None
        self.previous_results.clear()

        log_dir = self.output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"{case_name}.log"
        self.log_path.write_text("")

    def next_response_id(self) -> int:
        rid = self.response_id
        self.response_id += 1
        return rid
