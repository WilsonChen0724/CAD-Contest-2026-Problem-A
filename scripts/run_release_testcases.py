from __future__ import annotations

import argparse
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
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
        "--case-range",
        action="append",
        default=[],
        help="Run a contiguous testcase range, e.g. --case-range test25-test40 or --case-range 25-40. May be repeated.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all release testcases explicitly.",
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
        default=300.0,
        help="Timeout in seconds for non-basic responses. Official default: 300 seconds.",
    )
    parser.add_argument(
        "--basic-timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds for basic begin/read/write responses. Official default: 60 seconds.",
    )
    parser.add_argument(
        "--validation-ledger",
        action="store_true",
        help="Pass --validation-ledger to main.py and copy validation records into runner_output.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = (repo_root / args.release_dir).resolve()
    testcase_root = release_dir / "testcase"
    if not testcase_root.exists():
        print(f"Release testcase directory not found: {testcase_root}", file=sys.stderr)
        return 2

    if args.all and (args.case or args.case_range):
        print("Use either --all or selected cases/ranges, not both.", file=sys.stderr)
        return 2
    if not args.all and not args.case and not args.case_range:
        print("No testcase selection provided. Use --all, --case testNN, or --case-range test25-test40.", file=sys.stderr)
        return 2

    try:
        selected_cases = _expand_selected_cases(args.case, args.case_range)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    case_dirs = _select_cases(testcase_root, selected_cases, run_all=args.all)
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
                basic_timeout=args.basic_timeout,
                validation_ledger=args.validation_ledger,
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


def _expand_selected_cases(cases: list[str], ranges: list[str]) -> list[str]:
    selected = list(cases)
    for item in ranges:
        selected.extend(_expand_case_range(item))
    return selected


def _expand_case_range(raw_range: str) -> list[str]:
    parts = raw_range.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f'Invalid --case-range "{raw_range}". Expected test25-test40 or 25-40.')
    start = _parse_case_number(parts[0], raw_range)
    end = _parse_case_number(parts[1], raw_range)
    if start > end:
        raise ValueError(f'Invalid --case-range "{raw_range}": start must be <= end.')
    return [f"test{number:02d}" for number in range(start, end + 1)]


def _parse_case_number(token: str, raw_range: str) -> int:
    normalized = token.strip().lower()
    if normalized.startswith("test"):
        normalized = normalized[4:]
    if not normalized.isdigit():
        raise ValueError(f'Invalid --case-range "{raw_range}": could not parse testcase number from "{token}".')
    return int(normalized)


def _select_cases(testcase_root: Path, selected: list[str], *, run_all: bool = False) -> list[Path]:
    requested = set(selected)
    case_dirs = sorted(path for path in testcase_root.iterdir() if path.is_dir())
    if run_all:
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
    basic_timeout: float,
    validation_ledger: bool,
) -> CaseResult:
    prompt_path = case_dir / "prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    prompt_text = prompt_path.read_text(encoding="utf-8")
    prompts = _prompt_lines(prompt_text)
    prompt_count = len(prompts)
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
    if validation_ledger:
        command.append("--validation-ledger")

    response_timeouts = [_timeout_for_prompt(prompt, basic_timeout, timeout) for prompt in prompts]
    stdout, stderr, returncode = _run_case_interactive(
        command=command,
        cwd=release_dir,
        prompts=prompts,
        response_timeouts=response_timeouts,
    )

    out_path = result_root / f"{case_dir.name}.stdout.txt"
    err_path = result_root / f"{case_dir.name}.stderr.txt"
    out_path.write_text(stdout, encoding="utf-8")
    err_path.write_text(stderr, encoding="utf-8")
    _copy_generated_netlist(release_dir, result_root, case_dir.name)
    _copy_validation_ledger(release_dir, result_root, case_dir.name)

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



def _timeout_for_prompt(prompt: str, basic_timeout: float, non_basic_timeout: float) -> float:
    return basic_timeout if _is_basic_operation_prompt(prompt) else non_basic_timeout


def _is_basic_operation_prompt(prompt: str) -> bool:
    text = prompt.strip().lower()
    words = set(re.findall(r"[a-z0-9_]+", text))
    if "testcase" in words and words & {"beginning", "begin", "start"}:
        return True
    if {"case", "name"}.issubset(words) and "testcase" in words:
        return True
    design_file_hint = any(hint in text for hint in (".v", "verilog", "netlist", "design", "file"))
    if words & {"load", "read"} and design_file_hint:
        return True
    write_intent = words & {"write", "save", "dump", "emit"} or "write out" in text
    output_file_hint = ".v" in text or re.search(r"\b[a-z0-9_./\\-]+_out\.v\b", text) is not None
    design_write_hint = any(
        phrase in text
        for phrase in (
            "current design",
            "modified design",
            "the design",
            "this design",
            "output netlist",
            "write netlist",
            "save netlist",
        )
    )
    output_design_intent = bool(
        re.search(r"\boutput\s+(?:the\s+)?(?:(?:current|modified)\s+)?design\b", text)
        or re.search(r"\boutput\s+(?:the\s+)?(?:current\s+)?netlist\b", text)
    )
    if (write_intent or output_design_intent) and (output_file_hint or design_write_hint):
        return True
    return False
def _copy_generated_netlist(release_dir: Path, result_root: Path, case_name: str) -> None:
    generated = release_dir / f"{case_name}_out.v"
    if generated.exists():
        target = result_root / generated.name
        target.write_bytes(generated.read_bytes())


def _copy_validation_ledger(release_dir: Path, result_root: Path, case_name: str) -> None:
    source = release_dir / "output" / "validation" / case_name
    if not source.exists():
        return
    target = result_root / "validation" / case_name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


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


def _prompt_lines(prompt_text: str) -> list[str]:
    return [line.strip() for line in prompt_text.splitlines() if line.strip()]


def _run_case_interactive(
    *,
    command: list[str],
    cwd: Path,
    prompts: list[str],
    response_timeouts: list[float],
) -> tuple[str, str, int]:
    """
    Run main.py once, feed prompts one by one, and enforce a timeout per response.

    The contest state is preserved because all prompts still go to the same
    subprocess. The runner only changes how timeout accounting is done.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_q: queue.Queue[str | None] = queue.Queue()
    stderr_q: queue.Queue[str | None] = queue.Queue()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_thread = _start_reader(process.stdout, stdout_q)
    stderr_thread = _start_reader(process.stderr, stderr_q)

    returncode = 0
    try:
        assert process.stdin is not None
        for response_id, prompt in enumerate(prompts, start=1):
            response_timeout = response_timeouts[response_id - 1]
            if process.poll() is not None:
                returncode = process.returncode or 1
                stderr_lines.append(
                    f"Process exited before response {response_id} with code {returncode}.\n"
                )
                break

            process.stdin.write(prompt + "\n")
            process.stdin.flush()
            timed_out = not _read_until_response_end(
                stdout_q=stdout_q,
                stderr_q=stderr_q,
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                response_id=response_id,
                timeout=response_timeout,
            )
            if timed_out:
                message = f"TimeoutExpired: response {response_id} exceeded {response_timeout:.1f} seconds."
                stderr_lines.append(message + "\n")
                _append_timeout_response(stdout_lines, response_id, message)
                _terminate_process(process)
                returncode = 124
                break
        else:
            process.stdin.close()
            try:
                returncode = process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                stderr_lines.append("TimeoutExpired: process did not exit after all prompts.\n")
                _terminate_process(process)
                returncode = 124
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()

    _drain_queue(stdout_q, stdout_lines)
    _drain_queue(stderr_q, stderr_lines)
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    _drain_queue(stdout_q, stdout_lines)
    _drain_queue(stderr_q, stderr_lines)
    return "".join(stdout_lines), "".join(stderr_lines), returncode


def _append_timeout_response(stdout_lines: list[str], response_id: int, message: str) -> None:
    response_text = "".join(stdout_lines)
    if f"#END {response_id}" in response_text:
        return
    if f"#RESPONSE {response_id}" not in response_text:
        stdout_lines.append(f"#RESPONSE {response_id}\n")
    stdout_lines.append(f"Error: {message}\n")
    stdout_lines.append(f"#END {response_id}\n")


def _start_reader(stream, out_queue: queue.Queue[str | None]) -> threading.Thread:
    def read_stream() -> None:
        try:
            if stream is not None:
                for line in stream:
                    out_queue.put(line)
        finally:
            out_queue.put(None)

    thread = threading.Thread(target=read_stream, daemon=True)
    thread.start()
    return thread


def _read_until_response_end(
    *,
    stdout_q: queue.Queue[str | None],
    stderr_q: queue.Queue[str | None],
    stdout_lines: list[str],
    stderr_lines: list[str],
    response_id: int,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    end_marker = f"#END {response_id}"
    while True:
        _drain_queue(stderr_q, stderr_lines)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            line = stdout_q.get(timeout=min(0.1, remaining))
        except queue.Empty:
            continue
        if line is None:
            return False
        stdout_lines.append(line)
        if line.strip() == end_marker:
            _drain_queue(stderr_q, stderr_lines)
            return True


def _drain_queue(source: queue.Queue[str | None], target: list[str]) -> None:
    while True:
        try:
            item = source.get_nowait()
        except queue.Empty:
            return
        if item is not None:
            target.append(item)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3.0)


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
