from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.llm_api import LLMAPIError, LLMNotConfiguredError
from agent.llm_planner import plan_with_llm
from agent.plan_checker import (
    PlanValidationError,
    format_plan_error,
    validate_plan,
)
from agent.planner import plan_request
from runtime.config import load_config
from runtime.dispatcher import dispatch_plan
from runtime.response import emit_response
from runtime.state import CurrentState


def main() -> int:
    """Run the stdin-driven contest request loop."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-config", dest="config", required=False)
    parser.add_argument(
        "-planner",
        "--planner",
        choices=("rule", "llm_openai", "llm_claude", "llm_both"),
        default="llm_both",
        help=(
            "rule: deterministic planner for local debugging; llm_openai: OpenAI planner; "
            "llm_claude: Claude planner; llm_both: OpenAI first, Claude fallback"
        ),
    )
    parser.add_argument(
        "--ensure-yosys",
        action="store_true",
        help="ensure a local or system Yosys is available before reading requests",
    )
    parser.add_argument(
        "--force-yosys-install",
        action="store_true",
        help="reinstall the local OSS CAD Suite when used with --ensure-yosys",
    )
    args = parser.parse_args()

    if args.ensure_yosys:
        _ensure_yosys_available(force=args.force_yosys_install)

    config = load_config(args.config)
    state = CurrentState()
    prompt = _load_prompt()

    for raw_line in sys.stdin:
        request = raw_line.strip()
        if not request:
            continue

        try:
            plan = _make_plan(request, state, config, prompt, args.planner)
            body = dispatch_plan(state, plan)
        except PlanValidationError as exc:
            body = format_plan_error(exc)
        except LLMNotConfiguredError as exc:
            body = f"LLM planner is not configured: {exc}"
        except LLMAPIError as exc:
            body = f"LLM API error: {exc}"
        except Exception as exc:
            body = f"Error: {exc}"

        print(emit_response(state, body), flush=True)

    return 0


def _make_plan(request: str, state: CurrentState, config: dict, prompt: str, planner_mode: str) -> dict:
    if planner_mode == "rule":
        return validate_plan(plan_request(request, state))

    if planner_mode == "llm_openai":
        return plan_with_llm(prompt, request, _with_provider(config, "openai"))

    if planner_mode == "llm_claude":
        return plan_with_llm(prompt, request, _with_provider(config, "anthropic"))

    if planner_mode == "llm_both":
        try:
            return plan_with_llm(prompt, request, _with_provider(config, "openai"))
        except (LLMNotConfiguredError, LLMAPIError, PlanValidationError) as openai_error:
            print(f"OpenAI planner failed; falling back to Claude: {openai_error}", file=sys.stderr)
            return plan_with_llm(prompt, request, _with_provider(config, "anthropic"))

    raise PlanValidationError(f"Unknown planner mode: {planner_mode}")


def _with_provider(config: dict, provider: str) -> dict:
    updated = dict(config)
    if "raw" in updated:
        raw = str(updated.get("raw", ""))
        updated["raw"] = _replace_provider(raw, provider)
    else:
        updated["provider"] = provider
    return updated


def _replace_provider(raw: str, provider: str) -> str:
    lines = []
    replaced = False
    for line in raw.splitlines():
        if line.strip().startswith("provider:"):
            lines.append(f'provider: "{provider}"')
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.insert(0, f'provider: "{provider}"')
    return "\n".join(lines)


def _load_prompt() -> str:
    return (Path(__file__).parent / "agent" / "prompt.txt").read_text(encoding="utf-8")


def _ensure_yosys_available(force: bool = False) -> None:
    """
    Ensure Yosys is available before the stdin response loop starts.

    Installer output is routed to stderr so contest response blocks on stdout
    remain clean.
    """
    repo_root = Path(__file__).resolve().parent
    local_bin_dir = repo_root / "third_party" / "yosys" / "oss-cad-suite" / "bin"
    local_yosys = local_bin_dir / ("yosys.exe" if os.name == "nt" else "yosys")

    if not force:
        system_yosys = shutil.which("yosys")
        if system_yosys:
            print(f"Yosys found: {system_yosys}", file=sys.stderr)
            return

        if local_yosys.exists():
            os.environ["PATH"] = str(local_bin_dir) + os.pathsep + os.environ.get("PATH", "")
            print(f"Using local Yosys: {local_yosys}", file=sys.stderr)
            return

    installer = repo_root / "scripts" / "install_yosys.py"
    command = [sys.executable, str(installer)]
    if force:
        command.append("--force")

    print("Yosys not found; installing local OSS CAD Suite...", file=sys.stderr)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError(f"Yosys installation failed with exit code {completed.returncode}.")

    os.environ["PATH"] = str(local_bin_dir) + os.pathsep + os.environ.get("PATH", "")
    if shutil.which("yosys") is None:
        raise RuntimeError("Yosys installation finished, but yosys is still not available on PATH.")


if __name__ == "__main__":
    raise SystemExit(main())
