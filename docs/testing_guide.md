# Testing Guide

This guide explains how to run the local tests and the `A_release testcase_0510` release-style testcases.

Assumptions:

- You are in the repository root: `CAD_Contest2026_Problem_A`.
- Python dependencies are installed in your active Python/conda environment.
- Yosys is already installed and available as `yosys` on `PATH`, or installed under `third_party/yosys/oss-cad-suite`.
- `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are not set yet unless you are running LLM tests.

## 1. Environment Check

```bash
python --version
yosys -V
python -m pip install -r requirements.txt
```

On WSL, a typical setup is:

```bash
conda activate eda
cd /mnt/c/Users/USER/Desktop/EDA_final_project/CAD_Contest2026_Problem_A
```

## 2. Run Local Unit Tests

Run the full local unit test suite:

```bash
python -m unittest discover -s tests
```

Run only planner/checker tests when working on LLM planning or tool schema changes:

```bash
python -m unittest tests.test_rule_planner tests.test_plan_checker tests.test_llm_planner
```

Run parser/writer-related tests only:

```bash
python -m unittest tests.test_parser
```

Parser and writer tests require Yosys.

## 3. Run the Local Smoke Test

The small smoke input is under `tests/smoke_input.txt` and uses `tests/design/netlist/test8.v`.

Linux/WSL/macOS:

```bash
python main.py -planner rule -config config.example.yaml < tests/smoke_input.txt
```

Windows PowerShell:

```powershell
Get-Content .\tests\smoke_input.txt | python .\main.py -planner rule -config .\config.example.yaml
```

Expected behavior:

- The program prints `#RESPONSE <id>` / `#END <id>` blocks to stdout.
- It loads `tests/design/netlist/test8.v`.
- It writes `output/test8_out.v`.

## 4. Run A_release Testcases with the Rule Planner

The release runner executes each `prompt.txt` line-by-line through `main.py`.
It uses `A_release testcase_0510` as the working directory, so prompt paths such as `testcase/test01/test01.v` resolve correctly.

Run one testcase:

```bash
python scripts/run_release_testcases.py --case test01 --planner rule
```

Run multiple selected testcases:

```bash
python scripts/run_release_testcases.py --case test01 --case test02 --planner rule
```

Run a selected range:

```bash
python scripts/run_release_testcases.py --case-range test25-test40 --planner rule
```

The runner now requires an explicit selection. Use `--all`, `--case`, or `--case-range`; this prevents accidental full-suite LLM runs.

Local default timeouts follow the contest response policy: 60 seconds for basic
begin/read/write responses and 300 seconds for non-basic analysis,
transformation, optimization, and verification responses. Override with
`--basic-timeout` and `--timeout` when needed. For an official-style beta run,
make the timeout profile explicit:

```bash
python scripts/run_release_testcases.py --all --planner llm_openai --basic-timeout 60 --timeout 300
```

Run all release testcases:

```bash
python scripts/run_release_testcases.py --all --planner rule
```

Outputs are written to:

```text
A_release testcase_0510/runner_output/<planner>/testNN.stdout.txt
A_release testcase_0510/runner_output/<planner>/testNN.stderr.txt
```

Generated netlists are written relative to `A_release testcase_0510`, following each prompt's requested output path. The release runner also copies `testNN_out.v` into `runner_output/<planner>/` when that file is generated, which keeps OpenAI and Claude outputs comparable.

## 5. Run A_release Testcases with LLM Planners

Set the API key for the provider you want to test. Do not commit real keys.

Temporary shell variable:

```bash
export OPENAI_API_KEY="your_openai_key_here"
export ANTHROPIC_API_KEY="your_anthropic_key_here"
```

Conda environment variable:

```bash
conda env config vars set OPENAI_API_KEY="your_openai_key_here"
conda env config vars set ANTHROPIC_API_KEY="your_anthropic_key_here"
conda deactivate
conda activate eda
```

Run one testcase with OpenAI:

```bash
python scripts/run_release_testcases.py --case test01 --planner llm_openai
```

Run test25 through test40 with OpenAI:

```bash
python scripts/run_release_testcases.py --case-range test25-test40 --planner llm_openai
```

Run all testcases with OpenAI:

```bash
python scripts/run_release_testcases.py --all --planner llm_openai
```

Run one testcase with Claude:

```bash
python scripts/run_release_testcases.py --case test01 --planner llm_claude
```

Run one testcase with both OpenAI and Claude for comparison:

```bash
python scripts/run_release_testcases.py --case test01 --planner llm_both
```

Planner modes:

- `rule`: deterministic local rules only.
- `llm_openai`: every request goes to OpenAI and must return one EDA domain function tool call.
- `llm_claude`: every request goes to Claude and must return one EDA domain `tool_use` call.
- `llm_both`: in `main.py`, try OpenAI first and fall back to Claude; in this release runner, run `llm_openai` and `llm_claude` separately so both providers have their own logs.

## 6. Inspect LLM Tool Calls

When `--planner llm_openai`, `--planner llm_claude`, or `--planner llm_both` calls a provider, raw tool-call traces are printed to stderr and captured by the release runner.

View the trace:

```bash
cat "A_release testcase_0510/runner_output/llm_openai/test01.stderr.txt"
```

Each trace block looks like:

```text
[llm-tool-call]
{
  "arguments": {
    "steps": [
      {
        "args": {
          "path": "testcase/test01/test01.v"
        },
        "op": "read_design",
        "save_as": null
      }
    ]
  },
  "event": "openai_domain_tool_call",
  "normalized_plan": {
    "steps": [
      {
        "args": {
          "path": "testcase/test01/test01.v"
        },
        "op": "read_design"
      }
    ]
  },
  "tool": "run_design_io_plan"
}
```

`arguments` is the raw provider tool-call payload. `normalized_plan` is the checked plan passed to the dispatcher.

## 7. Strict Regression Options

During development, unsupported responses are counted but do not fail the release runner. Once backend coverage improves, use stricter flags:

```bash
python scripts/run_release_testcases.py --all --planner rule --fail-on-unsupported --fail-on-error
```

Stop on the first failing testcase:

```bash
python scripts/run_release_testcases.py --all --planner rule --fail-on-unsupported --fail-on-error --stop-on-fail
```
