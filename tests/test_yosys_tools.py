from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parser import yosys_tools


class YosysToolsTest(unittest.TestCase):
    def test_resolve_yosys_uses_windows_executable_and_lib_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_root = Path(tmp) / "third_party" / "yosys" / "oss-cad-suite"
            bin_dir = suite_root / "bin"
            lib_dir = suite_root / "lib"
            bin_dir.mkdir(parents=True)
            lib_dir.mkdir()
            yosys_exe = bin_dir / "yosys.exe"
            yosys_exe.write_text("", encoding="utf-8")

            with _mock_repo_and_system(tmp, "Windows"):
                resolved, env = yosys_tools._resolve_yosys()

        self.assertEqual(Path(resolved), yosys_exe)
        path_entries = env["PATH"].split(os.pathsep)
        self.assertEqual(path_entries[0], str(bin_dir))
        self.assertEqual(path_entries[1], str(lib_dir))

    def test_resolve_yosys_uses_linux_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_root = Path(tmp) / "third_party" / "yosys" / "oss-cad-suite"
            bin_dir = suite_root / "bin"
            bin_dir.mkdir(parents=True)
            yosys_bin = bin_dir / "yosys"
            yosys_bin.write_text("", encoding="utf-8")

            with _mock_repo_and_system(tmp, "Linux"):
                resolved, env = yosys_tools._resolve_yosys()

        self.assertEqual(Path(resolved), yosys_bin)
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(bin_dir))


class _mock_repo_and_system:
    def __init__(self, repo_root: str, system: str) -> None:
        self.repo_root = Path(repo_root)
        self.system = system
        self._patches = [
            patch.object(yosys_tools, "__file__", str(self.repo_root / "parser" / "yosys_tools.py")),
            patch("parser.yosys_tools.platform.system", return_value=system),
            patch("parser.yosys_tools.shutil.which", return_value=None),
        ]

    def __enter__(self) -> None:
        for item in self._patches:
            item.__enter__()

    def __exit__(self, exc_type, exc, tb) -> None:
        for item in reversed(self._patches):
            item.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
