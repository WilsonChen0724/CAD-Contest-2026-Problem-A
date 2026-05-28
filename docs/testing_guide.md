# Testing Guide

This guide explains how to run the local tests and the `A_release testcase_0510` release-style testcases.

Assumptions:

- You are in the repository root: `CAD_Contest2026_Problem_A`.
- Python dependencies are installed in your active Python/conda environment.
- Yosys is already installed and available as `yosys` on `PATH`, or installed under `third_party/yosys/oss-cad-suite`.
- `OPENAI_API_KEY` is not set yet unless you are running LLM tests.

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

Run all release testcases:

```bash
python scripts/run_release_testcases.py --planner rule
```

Outputs are written to:

```text
A_release testcase_0510/runner_output/testNN.stdout.txt
A_release testcase_0510/runner_output/testNN.stderr.txt
```

Generated netlists are written relative to `A_release testcase_0510`, following each prompt's requested output path.

## 5. Run A_release Testcases with the LLM Planner

Set your OpenAI API key first. Do not commit real keys.

Temporary shell variable:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

Conda environment variable:

```bash
conda env config vars set OPENAI_API_KEY="your_api_key_here"
conda deactivate
conda activate eda
```

Run one testcase with the LLM planner:

```bash
python scripts/run_release_testcases.py --case test01 --planner llm
```

Run one testcase with hybrid planning:

```bash
python scripts/run_release_testcases.py --case test01 --planner hybrid
```

Planner modes:

- `rule`: deterministic local rules only.
- `llm`: every request goes to OpenAI and must return one EDA domain tool call.
- `hybrid`: try rule planning first; call the LLM only when the rule planner returns `unsupported`.

## 6. Inspect LLM Tool Calls

When `--planner llm` or an LLM fallback in `--planner hybrid` calls OpenAI, raw tool-call traces are printed to stderr and captured by the release runner.

View the trace:

```bash
cat "A_release testcase_0510/runner_output/test01.stderr.txt"
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

`arguments` is the raw OpenAI tool-call payload. `normalized_plan` is the checked plan passed to the dispatcher.

## 7. Strict Regression Options

During development, unsupported responses are counted but do not fail the release runner. Once backend coverage improves, use stricter flags:

```bash
python scripts/run_release_testcases.py --planner rule --fail-on-unsupported --fail-on-error
```

Stop on the first failing testcase:

```bash
python scripts/run_release_testcases.py --planner rule --fail-on-unsupported --fail-on-error --stop-on-fail
```