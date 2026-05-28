from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CaseResult:
    name: str
    returncode: int
    responses: int
    unsupported: int
    errors: int
    output_path: Path
    stderr_path: Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run A_release testcase_0510 prompts through main.py."
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path("A_release testcase_0510"),
        help="Directory containing README.md and testcase/testNN folders.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run only one testcase name, e.g. --case test01. May be repeated.",
    )
    parser.add_argument(
        "--planner",
        choices=("rule", "llm", "hybrid"),
        default="llm",
        help="Planner mode passed to main.py.",
    )
    parser.add_argument(
        "--config",
        default="config.example.yaml",
        help="Config path passed to main.py.",
    )
    parser.add_argument(
        "--ensure-yosys",
        action="store_true",
        help="Pass --ensure-yosys to main.py before each testcase run.",
    )
    parser.add_argument(
        "--fail-on-unsupported",
        action="store_true",
        help="Return nonzero if any response says the request was unsupported.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return nonzero if any response contains an Error line.",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop after the first testcase with a failing return code or flagged response.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = (repo_root / args.release_dir).resolve()
    testcase_root = release_dir / "testcase"
    if not testcase_root.exists():
        print(f"Release testcase directory not found: {testcase_root}", file=sys.stderr)
        return 2

    case_dirs = _select_cases(testcase_root, args.case)
    if not case_dirs:
        print("No testcase directories matched.", file=sys.stderr)
        return 2

    result_root = release_dir / "runner_output"
    result_root.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    for case_dir in case_dirs:
        result = _run_case(
            repo_root=repo_root,
            release_dir=release_dir,
            result_root=result_root,
            case_dir=case_dir,
            planner=args.planner,
            config=args.config,
            ensure_yosys=args.ensure_yosys,
        )
        results.append(result)
        _print_case_summary(result)
        if args.stop_on_fail and _is_failure(result, args.fail_on_unsupported, args.fail_on_error):
            break

    _print_total_summary(results)
    return 1 if any(_is_failure(r, args.fail_on_unsupported, args.fail_on_error) for r in results) else 0


def _select_cases(testcase_root: Path, selected: list[str]) -> list[Path]:
    requested = set(selected)
    case_dirs = sorted(path for path in testcase_root.iterdir() if path.is_dir())
    if not requested:
        return case_dirs
    return [path for path in case_dirs if path.name in requested]


def _run_case(
    *,
    repo_root: Path,
    release_dir: Path,
    result_root: Path,
    case_dir: Path,
    planner: str,
    config: str,
    ensure_yosys: bool,
) -> CaseResult:
    prompt_path = case_dir / "prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    command = [
        sys.executable,
        str(repo_root / "main.py"),
        "-planner",
        planner,
        "-config",
        str(repo_root / config),
    ]
    if ensure_yosys:
        command.append("--ensure-yosys")

    completed = subprocess.run(
        command,
        cwd=release_dir,
        input=prompt_path.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
    )

    out_path = result_root / f"{case_dir.name}.stdout.txt"
    err_path = result_root / f"{case_dir.name}.stderr.txt"
    out_path.write_text(completed.stdout, encoding="utf-8")
    err_path.write_text(completed.stderr, encoding="utf-8")

    return CaseResult(
        name=case_dir.name,
        returncode=completed.returncode,
        responses=completed.stdout.count("#RESPONSE"),
        unsupported=completed.stdout.count("could not map"),
        errors=completed.stdout.count("Error:") + completed.stdout.count("Tool call rejected"),
        output_path=out_path,
        stderr_path=err_path,
    )


def _is_failure(result: CaseResult, fail_on_unsupported: bool, fail_on_error: bool) -> bool:
    if result.returncode != 0:
        return True
    if fail_on_unsupported and result.unsupported:
        return True
    if fail_on_error and result.errors:
        return True
    return False


def _print_case_summary(result: CaseResult) -> None:
    print(
        f"{result.name}: rc={result.returncode}, responses={result.responses}, "
        f"unsupported={result.unsupported}, errors={result.errors}"
    )


def _print_total_summary(results: list[CaseResult]) -> None:
    print("")
    print(f"Ran {len(results)} release testcase(s).")
    print(f"Responses: {sum(item.responses for item in results)}")
    print(f"Unsupported responses: {sum(item.unsupported for item in results)}")
    print(f"Error responses: {sum(item.errors for item in results)}")
    if results:
        print(f"Detailed outputs: {results[0].output_path.parent}")


if __name__ == "__main__":
    raise SystemExit(main())
