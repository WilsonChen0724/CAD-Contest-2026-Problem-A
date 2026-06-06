from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.run_release_testcases import _copy_generated_netlist, _planner_runs


class ReleaseRunnerTest(unittest.TestCase):
    def test_llm_both_runs_openai_and_claude_separately(self) -> None:
        self.assertEqual(_planner_runs("llm_both"), ["llm_openai", "llm_claude"])
        self.assertEqual(_planner_runs("llm_openai"), ["llm_openai"])

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


if __name__ == "__main__":
    unittest.main()