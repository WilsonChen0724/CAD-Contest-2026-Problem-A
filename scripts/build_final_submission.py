from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import stat
import tarfile
from pathlib import Path


PACKAGE_DIRS = ("agent", "eda", "parser", "runtime")
ROOT_FILES = ("cada1070_final", "main.py")
PYTHON_ARCHIVE_PATTERN = (
    "cpython-3.11*-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
FIXED_MTIME = 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the self-contained cada1070 final submission."
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("dist"),
        help="Output directory (default: dist).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    dist_dir = args.dist_dir
    if not dist_dir.is_absolute():
        dist_dir = repo_root / dist_dir
    dist_dir = dist_dir.resolve()
    staging = dist_dir / "final_test_submission"
    archive = dist_dir / "cada1070_final_submission.tar.gz"

    _validate_source_assets(repo_root)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for name in ROOT_FILES:
        _copy_file(repo_root / name, staging / name)
    _normalize_launcher(staging / "cada1070_final")

    for package_dir in PACKAGE_DIRS:
        _copy_tree(repo_root / package_dir, staging / package_dir)

    python_archive = sorted(
        (repo_root / "third_party" / "python").glob(PYTHON_ARCHIVE_PATTERN)
    )[0]
    _copy_file(
        python_archive,
        staging / "third_party" / "python" / python_archive.name,
    )
    _copy_z3_runtime(repo_root / "vendor" / "python", staging / "vendor" / "python")

    manifest_path = staging / "SUBMISSION_MANIFEST.txt"
    manifest_path.write_text(_build_manifest(staging), encoding="utf-8", newline="\n")
    _set_file_mode(manifest_path, executable=False)

    dist_dir.mkdir(parents=True, exist_ok=True)
    _write_reproducible_archive(staging, archive)
    _validate_output(staging, archive)

    print(f"Submission directory: {staging}")
    print(f"Submission archive:   {archive}")
    print(f"Archive SHA-256:      {_sha256(archive)}")
    print(f"Archive size:         {archive.stat().st_size} bytes")
    return 0


def _validate_source_assets(repo_root: Path) -> None:
    missing = [str(repo_root / name) for name in ROOT_FILES if not (repo_root / name).is_file()]
    for package_dir in PACKAGE_DIRS:
        if not (repo_root / package_dir / "__init__.py").is_file():
            missing.append(str(repo_root / package_dir / "__init__.py"))

    python_dir = repo_root / "third_party" / "python"
    archives = sorted(python_dir.glob(PYTHON_ARCHIVE_PATTERN))
    if len(archives) != 1:
        missing.append(
            f"exactly one {python_dir / PYTHON_ARCHIVE_PATTERN} (found {len(archives)})"
        )
    if not (repo_root / "vendor" / "python" / "z3" / "__init__.py").is_file():
        missing.append(str(repo_root / "vendor" / "python" / "z3" / "__init__.py"))
    if not (repo_root / "vendor" / "python" / "z3" / "lib" / "libz3.so").is_file():
        missing.append(str(repo_root / "vendor" / "python" / "z3" / "lib" / "libz3.so"))
    if missing:
        raise FileNotFoundError("Missing final-submission asset(s):\n- " + "\n- ".join(missing))


def _copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.is_dir():
            (destination / relative).mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file() or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        _copy_file(path, destination / relative)


def _copy_z3_runtime(source: Path, destination: Path) -> None:
    """Copy only files needed by ``import z3`` on Linux.

    The wheel also carries a duplicate versioned library, C/C++ headers, and a
    standalone CLI.  The contest program imports the Python API only, so those
    files would add tens of megabytes without changing runtime behavior.
    """
    z3_package = source / "z3"
    for path in sorted(z3_package.glob("*.py")):
        _copy_file(path, destination / "z3" / path.name)
    _copy_file(
        z3_package / "lib" / "libz3.so",
        destination / "z3" / "lib" / "libz3.so",
    )
    metadata = source / "z3_solver-4.15.4.0.dist-info" / "METADATA"
    if metadata.is_file():
        _copy_file(
            metadata,
            destination / "z3_solver-4.15.4.0.dist-info" / "METADATA",
        )


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    executable = source.name == "cada1070_final" or source.suffix == ".so"
    _set_file_mode(destination, executable=executable)


def _normalize_launcher(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")
    _set_file_mode(path, executable=True)


def _set_file_mode(path: Path, *, executable: bool) -> None:
    os.chmod(path, 0o755 if executable else 0o644)


def _build_manifest(staging: Path) -> str:
    lines = [
        "CAD Contest 2026 Problem A - Team 1070 Final Submission",
        "Entrypoint: ./cada1070_final -config <config_file_path>",
        "Target: RedHat 8, Linux x86_64",
        "",
        "SHA-256  PATH",
    ]
    for path in _staged_files(staging):
        if path.name == "SUBMISSION_MANIFEST.txt":
            continue
        lines.append(f"{_sha256(path)}  {path.relative_to(staging).as_posix()}")
    return "\n".join(lines) + "\n"


def _write_reproducible_archive(staging: Path, archive: Path) -> None:
    if archive.exists():
        archive.unlink()
    with archive.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=FIXED_MTIME) as gzip_file:
            with tarfile.open(fileobj=gzip_file, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in _staged_files(staging):
                    relative = path.relative_to(staging).as_posix()
                    info = tar.gettarinfo(str(path), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.mtime = FIXED_MTIME
                    info.mode = 0o755 if relative == "cada1070_final" or path.suffix == ".so" else 0o644
                    with path.open("rb") as source:
                        tar.addfile(info, source)


def _staged_files(staging: Path) -> list[Path]:
    return sorted(path for path in staging.rglob("*") if path.is_file())


def _validate_output(staging: Path, archive: Path) -> None:
    launcher = staging / "cada1070_final"
    if launcher.read_bytes().startswith(b"#!") is False:
        raise RuntimeError("cada1070_final does not start with a shebang.")
    if b"\r\n" in launcher.read_bytes():
        raise RuntimeError("cada1070_final contains CRLF line endings.")
    if os.name != "nt" and not (launcher.stat().st_mode & stat.S_IXUSR):
        raise RuntimeError("cada1070_final is not executable in the staging directory.")

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = {member.name for member in members}
        if "cada1070_final" not in names or "main.py" not in names:
            raise RuntimeError("Archive is missing its root entrypoint or main.py.")
        launcher_member = tar.getmember("cada1070_final")
        if launcher_member.mode & 0o111 == 0:
            raise RuntimeError("Archive entry cada1070_final is not executable.")
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"Unsafe archive member: {member.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
