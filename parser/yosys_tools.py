from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def run_yosys_script(script: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a Yosys script with the local OSS CAD Suite environment when present."""
    yosys, env = _resolve_yosys()
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        script_path = work_dir / "run.ys"
        script_path.write_text(script, encoding="utf-8")
        return subprocess.run(
            [str(yosys), "-q", "-s", str(script_path)],
            cwd=cwd or work_dir,
            env=env,
            text=True,
            capture_output=True,
        )


def quote_yosys_path(path: str | Path) -> str:
    """Quote a filesystem path for use in a Yosys script."""
    value = Path(path).resolve().as_posix()
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _resolve_yosys() -> tuple[Path | str, dict[str, str]]:
    """Resolve the Yosys executable for the current OS and its environment."""
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[1]
    suite_root = repo_root / "third_party" / "yosys" / "oss-cad-suite"
    system = platform.system().lower()

    if system == "windows":
        local_yosys = suite_root / "bin" / "yosys.exe"
        if local_yosys.exists():
            env = _with_path_entries(env, [suite_root / "bin", suite_root / "lib"])
            env.setdefault("YOSYSHQ_ROOT", str(suite_root) + os.sep)
            return local_yosys, env
    elif system == "linux":
        local_yosys = suite_root / "bin" / "yosys"
        if local_yosys.exists():
            env = _with_path_entries(env, [suite_root / "bin"])
            env.setdefault("YOSYSHQ_ROOT", str(suite_root) + os.sep)
            return local_yosys, env
    else:
        raise RuntimeError(
            f"Unsupported operating system for local Yosys resolution: {platform.system()}. "
            "Supported operating systems are Windows and Linux."
        )

    system_yosys = shutil.which("yosys")
    if system_yosys:
        return system_yosys, env

    raise RuntimeError(
        "Yosys was not found. Run `python scripts/install_yosys.py` first, "
        "or add yosys to PATH."
    )


def _with_path_entries(env: dict[str, str], entries: list[Path]) -> dict[str, str]:
    updated = env.copy()
    updated["PATH"] = os.pathsep.join([str(entry) for entry in entries] + [updated.get("PATH", "")])
    return updated
