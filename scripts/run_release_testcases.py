from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CaseResult:
    name: str
    planner: str
    returncode: int
    prompts: int
    responses: int
    missing_responses: int
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
        "--all",
        action="store_true",
        help="Run all release testcases explicitly. If neither --case nor --all is provided, all cases still run for backward compatibility.",
    )
    parser.add_argument(
        "--planner",
        choices=("rule", "llm_openai", "llm_claude", "llm_both"),
        default="rule",
        help="Planner mode passed to main.py. In this runner, llm_both runs llm_openai and llm_claude separately and stores both outputs.",
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
        help="Return nonzero if any response contains an error/config-failure marker.",
    )
    parser.add_argument(
        "--fail-on-response-mismatch",
        action="store_true",
        help="Return nonzero if the number of #RESPONSE blocks does not match prompt lines.",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop after the first testcase with a failing return code or flagged response.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for each testcase subprocess.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = (repo_root / args.release_dir).resolve()
    testcase_root = release_dir / "testcase"
    if not testcase_root.exists():
        print(f"Release testcase directory not found: {testcase_root}", file=sys.stderr)
        return 2

    if args.all and args.case:
        print("Use either --all or --case, not both.", file=sys.stderr)
        return 2

    case_dirs = _select_cases(testcase_root, args.case)
    if not case_dirs:
        print("No testcase directories matched.", file=sys.stderr)
        return 2

    planner_runs = _planner_runs(args.planner)

    results: list[CaseResult] = []
    should_stop = False
    for planner in planner_runs:
        result_root = release_dir / "runner_output" / planner
        result_root.mkdir(parents=True, exist_ok=True)
        print(f"\n=== Planner: {planner} ===", flush=True)
        for case_dir in case_dirs:
            print(f"Running {case_dir.name} with {planner}...", flush=True)
            result = _run_case(
                repo_root=repo_root,
                release_dir=release_dir,
                result_root=result_root,
                case_dir=case_dir,
                planner=planner,
                config=args.config,
                ensure_yosys=args.ensure_yosys,
                timeout=args.timeout,
            )
            results.append(result)
            _print_case_summary(result)
            if args.stop_on_fail and _is_failure(
                result,
                args.fail_on_unsupported,
                args.fail_on_error,
                args.fail_on_response_mismatch,
            ):
                should_stop = True
                break
        if should_stop:
            break

    _print_total_summary(results)
    return 1 if any(
        _is_failure(
            r,
            args.fail_on_unsupported,
            args.fail_on_error,
            args.fail_on_response_mismatch,
        )
        for r in results
    ) else 0


def _planner_runs(planner: str) -> list[str]:
    if planner == "llm_both":
        return ["llm_openai", "llm_claude"]
    return [planner]


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
    timeout: float,
) -> CaseResult:
    prompt_path = case_dir / "prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    prompt_text = prompt_path.read_text(encoding="utf-8")
    prompt_count = _count_prompt_lines(prompt_text)
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

    try:
        completed = subprocess.run(
            command,
            cwd=release_dir,
            input=prompt_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr = (
            f"{stderr}\nTimeoutExpired: testcase exceeded {timeout:.1f} seconds."
        ).strip()
        returncode = 124

    out_path = result_root / f"{case_dir.name}.stdout.txt"
    err_path = result_root / f"{case_dir.name}.stderr.txt"
    out_path.write_text(stdout, encoding="utf-8")
    err_path.write_text(stderr, encoding="utf-8")
    _copy_generated_netlist(release_dir, result_root, case_dir.name)

    return CaseResult(
        name=case_dir.name,
        planner=planner,
        returncode=returncode,
        prompts=prompt_count,
        responses=stdout.count("#RESPONSE"),
        missing_responses=max(0, prompt_count - stdout.count("#RESPONSE")),
        unsupported=stdout.count("could not map"),
        errors=_count_error_markers(stdout, stderr),
        output_path=out_path,
        stderr_path=err_path,
    )


def _copy_generated_netlist(release_dir: Path, result_root: Path, case_name: str) -> None:
    generated = release_dir / f"{case_name}_out.v"
    if generated.exists():
        target = result_root / generated.name
        target.write_bytes(generated.read_bytes())


def _is_failure(
    result: CaseResult,
    fail_on_unsupported: bool,
    fail_on_error: bool,
    fail_on_response_mismatch: bool,
) -> bool:
    if result.returncode != 0:
        return True
    if fail_on_unsupported and result.unsupported:
        return True
    if fail_on_error and result.errors:
        return True
    if fail_on_response_mismatch and result.responses != result.prompts:
        return True
    return False


def _print_case_summary(result: CaseResult) -> None:
    print(
        f"{result.planner}/{result.name}: rc={result.returncode}, prompts={result.prompts}, "
        f"responses={result.responses}, missing={result.missing_responses}, "
        f"unsupported={result.unsupported}, errors={result.errors}"
    )


def _print_total_summary(results: list[CaseResult]) -> None:
    print("")
    print(f"Ran {len(results)} release testcase run(s).")
    print(f"Prompts: {sum(item.prompts for item in results)}")
    print(f"Responses: {sum(item.responses for item in results)}")
    print(f"Missing responses: {sum(item.missing_responses for item in results)}")
    print(f"Unsupported responses: {sum(item.unsupported for item in results)}")
    print(f"Error responses: {sum(item.errors for item in results)}")
    if results:
        output_dirs = sorted({str(item.output_path.parent) for item in results})
        print("Detailed outputs:")
        for output_dir in output_dirs:
            print(f"- {output_dir}")


def _count_prompt_lines(prompt_text: str) -> int:
    return sum(1 for line in prompt_text.splitlines() if line.strip())


def _count_error_markers(stdout: str, stderr: str) -> int:
    text = f"{stdout}\n{stderr}"
    markers = [
        "Error:",
        "Tool call rejected",
        "LLM planner is not configured",
        "Traceback",
        "TimeoutExpired",
        "No design has been loaded",
        "FileNotFoundError",
        "RuntimeError",
        "ValueError",
    ]
    return sum(text.count(marker) for marker in markers)


if __name__ == "__main__":
    raise SystemExit(main())
