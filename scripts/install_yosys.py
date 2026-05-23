from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install Yosys for the current operating system."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove an existing local OSS CAD Suite install before installing.",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="Install under this directory instead of third_party/yosys.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    system = platform.system().lower()

    if system == "windows":
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts_dir / "install_yosys.ps1"),
        ]
        if args.install_dir is not None:
            command.extend(["-InstallDir", str(args.install_dir)])
        if args.force:
            command.append("-Force")
    elif system == "linux":
        command = ["bash", str(scripts_dir / "install_yosys.sh")]
        if args.install_dir is not None:
            command.extend(["--install-dir", str(args.install_dir)])
        if args.force:
            command.append("--force")
    else:
        print(
            f"Unsupported operating system for automatic Yosys install: {platform.system()}",
            file=sys.stderr,
        )
        print("Supported operating systems: Windows and Linux.", file=sys.stderr)
        return 1

    print("Running:", " ".join(command))
    completed = subprocess.run(command, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
