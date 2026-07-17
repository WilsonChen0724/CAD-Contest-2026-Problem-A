from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from runtime.response import emit_response
from runtime.state import CurrentState


class ResponseLogTest(unittest.TestCase):
    def test_testcase_log_is_created_in_process_working_directory(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                state = CurrentState()

                state.begin_testcase("test01")
                block = emit_response(state, "Initialized testcase.")

                expected_log = Path(tmp) / "test01.log"
                self.assertEqual(state.log_path, Path("test01.log"))
                self.assertTrue(expected_log.is_file())
                self.assertEqual(
                    expected_log.read_text(encoding="utf-8"),
                    block + "\n",
                )
                self.assertFalse((Path(tmp) / "output" / "logs" / "test01.log").exists())
            finally:
                os.chdir(original_cwd)

    def test_contest_entrypoint_leaves_discoverable_case_log(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(repo_root / "main.py"), "--planner", "rule"],
                cwd=tmp,
                input="This is the beginning of a new testcase. The case name is test01.\n",
                text=True,
                capture_output=True,
                check=False,
            )

            expected_log = Path(tmp) / "test01.log"
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(expected_log.is_file())
            self.assertEqual(expected_log.read_text(encoding="utf-8"), completed.stdout)
            self.assertIn("#RESPONSE 1", completed.stdout)
            self.assertIn("#END 1", completed.stdout)


if __name__ == "__main__":
    unittest.main()
