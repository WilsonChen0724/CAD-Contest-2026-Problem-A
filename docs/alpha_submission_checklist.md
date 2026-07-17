# Alpha Submission Checklist

Source: `Alpha Test Submission Guideline_ABC.pdf`.

## What the Guideline Requires

- Deadline: June 12, 2026, 17:00 GMT+8.
- Problem A submissions must be placed in the server-provided folder named `alpha_test_submission` under the group's home directory.
- Do not rename the `alpha_test_submission` folder.
- Do not add an extra compressed archive inside the target folder unless the official Problem A statement separately requires it.
- Cloud and email submissions are not accepted.
- Re-submissions caused by file naming mistakes are not accepted, so verify names before upload.

## Current Project Submission Entry

- Executable launcher: `cada1070_alpha`
- Git executable mode: should be `100755`.
- Expected invocation:

```bash
./cada1070_alpha -config <config_file_path>
```

The launcher calls:

```bash
python3 "$(dirname "$0")/main.py" "$@"
```

`main.py` reads requests from stdin and writes contest-style `#RESPONSE <id>` / `#END <id>` blocks to stdout.

## Files / Folders To Include

Include the source tree needed by the launcher:

- `cada1070_alpha`
- `main.py`
- `agent/`
- `runtime/`
- `eda/`
- `parser/`
- `requirements.txt`
- any required official config file, for example `config.yaml`
- project documentation if allowed/useful: `README.md`, `docs/`

Do not include development outputs unless explicitly requested:

- `__pycache__/`
- `output/`
- `A_release testcase_0510/runner_output/`
- local API-key files committed to Git
- downloaded local OSS CAD Suite folders unless the official environment requires a bundled Yosys

## Environment Checks Before Upload

Additional Q&A constraints for Problem A:

- The official Docker working directory is `/app`.
- Input files are mounted under `/app`; read the design from the path given in
  the prompt.
- Write each testcase log directly in the process working directory as
  `<case_name>.log`; do not place it under `output/` or another subdirectory.
- Write generated netlists to the testcase path requested by the prompt.
- Evaluation has internet access only to the designated model API endpoints, so
  do not rely on `pip install`, Yosys download, or other general internet access
  during evaluation.
- The released named-pin DFF style such as `.RN`, `.SN`, `.CK`, `.D`, and `.Q`
  is the correct testcase format and should remain supported.

Run these in the same environment intended for submission:

```bash
python3 -m pip install -r requirements.txt
python3 -c "import z3; print(z3.get_version_string())"
yosys -V
chmod +x cada1070_alpha
./cada1070_alpha -config config.yaml < tests/smoke_input.txt
```

For local deterministic debugging only:

```bash
python3 main.py -planner rule -config config.example.yaml < tests/smoke_input.txt
```

## Current Notes

- `main.py` defaults to `llm_both` so the official `./cada1070_alpha -config ...` invocation uses the LLM planner path.
- `-planner rule` remains available for local tests that should not call external LLM APIs.
- `requirements.txt` includes `z3-solver`; each teammate still needs to install it in their active Python environment.
