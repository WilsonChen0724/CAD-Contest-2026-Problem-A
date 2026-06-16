from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.run_release_testcases import (
    _append_timeout_response,
    _copy_generated_netlist,
    _expand_case_range,
    _expand_selected_cases,
    _planner_runs,
    _select_cases,
    _timeout_for_prompt,
)


class ReleaseRunnerTest(unittest.TestCase):
    def test_llm_both_runs_openai_and_claude_separately(self) -> None:
        self.assertEqual(_planner_runs("llm_both"), ["llm_openai", "llm_claude"])
        self.assertEqual(_planner_runs("llm_openai"), ["llm_openai"])

    def test_expands_case_ranges(self) -> None:
        self.assertEqual(
            _expand_case_range("test25-test28"),
            ["test25", "test26", "test27", "test28"],
        )
        self.assertEqual(_expand_case_range("25-26"), ["test25", "test26"])
        self.assertEqual(
            _expand_selected_cases(["test31"], ["test33-test34"]),
            ["test31", "test33", "test34"],
        )

    def test_select_cases_requires_explicit_selection_unless_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("test01", "test25", "test26"):
                (root / name).mkdir()

            selected = _select_cases(root, ["test25", "test26"])
            all_cases = _select_cases(root, [], run_all=True)
            none = _select_cases(root, [])

            self.assertEqual([path.name for path in selected], ["test25", "test26"])
            self.assertEqual([path.name for path in all_cases], ["test01", "test25", "test26"])
            self.assertEqual(none, [])


    def test_timeout_for_prompt_matches_official_basic_and_non_basic_limits(self) -> None:
        self.assertEqual(_timeout_for_prompt("This is the beginning of testcase test25.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Please load the design from test25.v.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Please write the current design to test25_out.v.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Perform depth optimization on the combinational logic.", 60.0, 300.0), 300.0)
        self.assertEqual(_timeout_for_prompt("What Boolean function does output n8 compute?", 60.0, 300.0), 300.0)

    def test_copies_generated_netlist_to_planner_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            result_root = release_dir / "runner_output" / "llm_openai"
            release_dir.mkdir()
            result_root.mkdir(parents=True)
            generated = release_dir / "test01_out.v"
            generated.write_text("module top; endmodule\n", encoding="utf-8")

            _copy_generated_netlist(release_dir, result_root, "test01")

            copied = result_root / "test01_out.v"
            self.assertEqual(copied.read_text(encoding="utf-8"), "module top; endmodule\n")

    def test_timeout_response_is_visible_in_stdout_log(self) -> None:
        stdout_lines: list[str] = []

        _append_timeout_response(stdout_lines, 6, "TimeoutExpired: response 6 exceeded 300.0 seconds.")

        self.assertEqual(
            "".join(stdout_lines),
            "#RESPONSE 6\n"
            "Error: TimeoutExpired: response 6 exceeded 300.0 seconds.\n"
            "#END 6\n",
        )


if __name__ == "__main__":
    unittest.main()
