from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.run_release_testcases import (
    _append_timeout_response,
    _clear_validation_ledger_source,
    _copy_generated_netlist,
    _copy_validation_ledger,
    _expand_case_range,
    _expand_selected_cases,
    _planner_runs,
    _resolve_release_dir,
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

    def test_auto_detects_current_public_release_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "A_release testcase_0510"
            (current / "testcase").mkdir(parents=True)

            self.assertEqual(_resolve_release_dir(root, None), current.resolve())

    def test_explicit_release_directory_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "custom_release"
            (explicit / "testcase").mkdir(parents=True)
            (root / "A_release testcase_0510" / "testcase").mkdir(parents=True)

            self.assertEqual(_resolve_release_dir(root, Path("custom_release")), explicit.resolve())


    def test_timeout_for_prompt_matches_official_basic_and_non_basic_limits(self) -> None:
        self.assertEqual(_timeout_for_prompt("This is the beginning of testcase test25.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Please load the design from test25.v.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Please write the current design to test25_out.v.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Output the design as top.v.", 60.0, 300.0), 60.0)
        self.assertEqual(_timeout_for_prompt("Perform depth optimization on the combinational logic.", 60.0, 300.0), 300.0)
        self.assertEqual(_timeout_for_prompt("What Boolean function does output n8 compute?", 60.0, 300.0), 60.0)
        self.assertEqual(
            _timeout_for_prompt("Find all paths from input n0 to output n8 in the design.", 60.0, 300.0),
            60.0,
        )
        self.assertEqual(
            _timeout_for_prompt("Report all primary outputs whose logic cone contains more than 100 gates.", 60.0, 300.0),
            60.0,
        )
        self.assertEqual(
            _timeout_for_prompt("Prove that the transformed design is equivalent to the pre-transformation netlist.", 60.0, 300.0),
            60.0,
        )
        self.assertEqual(
            _timeout_for_prompt("How many dangling gates were removed?", 60.0, 300.0),
            60.0,
        )
        self.assertEqual(
            _timeout_for_prompt("How many BUF gates were added by the buffer insertion just performed?", 60.0, 300.0),
            60.0,
        )
        self.assertEqual(
            _timeout_for_prompt("Replace every XOR gate with an equivalent NAND-only implementation.", 60.0, 300.0),
            300.0,
        )
        self.assertEqual(
            _timeout_for_prompt("Find all back-to-back inverter pairs and collapse them into a wire.", 60.0, 300.0),
            300.0,
        )

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

    def test_copies_validation_ledger_to_planner_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            result_root = release_dir / "runner_output" / "rule"
            source = release_dir / "output" / "validation" / "test01"
            source.mkdir(parents=True)
            result_root.mkdir(parents=True)
            (source / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

            _copy_validation_ledger(release_dir, result_root, "test01")

            copied = result_root / "validation" / "test01" / "ledger.jsonl"
            self.assertEqual(copied.read_text(encoding="utf-8"), "{}\n")

    def test_clears_validation_ledger_source_before_case_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            source = release_dir / "output" / "validation" / "test01"
            source.mkdir(parents=True)
            (source / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

            _clear_validation_ledger_source(release_dir, "test01")

            self.assertFalse(source.exists())

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
