# Person C Work Plan

Owner scope:

- Runtime integration
- Testcase state and response/log handling
- Transformation interface
- Verification interface
- Release runner and external validator
- Transform guard and optimization metrics reporting

## Beta P0 Status

Person C's current beta responsibility is to make the finished testcase run
externally checkable. The main deliverable is not only "the program printed an
answer"; it is a ledger-backed validation result that can be discussed in the
final report.

Official timeout profile:

- Basic operations from Section 4.1: 60 seconds.
- All other analysis, transformation, optimization, and verification requests:
  300 seconds.

Primary beta P0 tasks:

1. Run the release suite with `--validation-ledger`.
2. Run `scripts\validate_release_outputs.py` on the saved ledgers.
3. Export both JSONL verdicts and CSV QoR metrics.
4. Fix transform-guard bugs when the validator reports a concrete `FAIL`.
5. Document any remaining `INCONCLUSIVE` result with the bounded-policy reason
   and the next stronger oracle needed.

Recommended Person C commands:

```powershell
python -m unittest discover -s tests
python scripts\run_release_testcases.py --all --planner rule --validation-ledger --fail-on-error --fail-on-unsupported --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner rule --all --output outputs\validator_rule.jsonl --metrics-output outputs\validator_rule_metrics.csv
python scripts\run_release_testcases.py --all --planner llm_openai --validation-ledger --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner llm_openai --all --output outputs\validator_llm_openai.jsonl --metrics-output outputs\validator_llm_openai_metrics.csv
```

Completion criteria:

- Unit tests pass.
- Rule-mode release run has zero missing, unsupported, and error responses.
- LLM-mode release run has zero missing and runtime error responses; any
  unsupported response is handed to Person B with the prompt, response id, and
  emitted tool-call trace.
- Validator outputs are saved under `outputs/` for discussion but are not
  committed unless the team intentionally wants a frozen report snapshot.
- Every transform/optimization `FAIL` is either fixed or assigned with oracle
  evidence from the validator report.

## Latest Local Person C Check

Date: 2026-07-10.

Completed:

- `python -m unittest discover -s tests`
  - Result: 235 tests passed.
- Rule-mode release runner with validation ledger and official timeout profile:
  - `--basic-timeout 60 --timeout 300`
  - Full run was split because the shell-level command timeout was shorter
    than the whole 40-case regression.
  - `test35-test40` summary: 114 prompts, 114 responses, 0 missing,
    0 unsupported, 0 errors.
  - All 40 rule stdout files were refreshed; a scan found no `Error`,
    `Unsupported`, or `Traceback` markers.
- Rule-mode offline validator:
  - Result after fixing pre-existing-connectivity handling:
    `PASS=418`, `FAIL=5`, `SKIP=0`, `INCONCLUSIVE=57` over 480 responses.
  - Metrics export was generated at `outputs\validator_rule_metrics.csv`.

Current validator FAIL items to assign or debug:

- `test17 response 15 [check_equivalence]`: Z3 oracle disagreement.
- `test33 response 5 [replace_xnor_nor_with_basic_gates]`: residual XNOR
  gates remain.
- `test35 response 18 [replace_xor_with_nand]`: residual XOR gates remain.
  The validator output currently records this duplicate response twice.
- `test39 response 18 [replace_xor_with_nand]`: residual XOR gates remain.

Person C completed in this pass:

- Updated beta timeout documentation to the official 60/300 policy.
- Recorded P0/P1 ownership for Person A, Person B, and Person C.
- Fixed validator large-design transform checking so pre-existing missing or
  duplicate drivers are not reported as transform failures unless the transform
  introduces a new connectivity regression.
- Changed bounded `report_all_paths` validation so hitting the path cap is
  `INCONCLUSIVE`, not a false exact-answer `FAIL`.

## Overall Priority

The first priority is to make the contest interaction loop stable:

1. Keep one evolving design state per testcase.
2. Accept natural-language requests from stdin.
3. Dispatch only valid EDA tool operations.
4. Emit every answer with `#RESPONSE <id>` and `#END <id>`.
5. Save the same response blocks into `<case_name>.log`.
6. Verify that netlist transformations do not leave broken connectivity.

## Day 2: Runtime Stability

Goals:

- Confirm `cada1070_alpha` is the official executable name.
- Strengthen `CurrentState` so it records testcase, config, log path, output path, and loaded design path.
- Reset response id to 1 at the beginning of each testcase.
- Add dispatcher-side validation for supported tool operations and required arguments.
- Reject invalid `targets_from` references instead of silently using an empty list.
- Keep all runtime errors inside the standard response wrapper.
- Make response/log writing use UTF-8 and avoid malformed response tags inside body text.

Done when:

- Smoke test runs from stdin through the runtime loop.
- Response ids and log file behavior match the contest requirement.
- Invalid tool calls return clear error messages.

## Day 3: Required Transformations

Goals:

- Implement `remove_dangling`.
- Implement `replace_inv_buf_with_inv`.
- Implement `replace_or_with_nand_not`.
- Rebuild driver/fanout graph after every transformation.
- Return detailed transformation summaries, including changed gates and nets.

Suggested order:

1. `remove_dangling`
2. `replace_inv_buf_with_inv`
3. `replace_or_with_nand_not`

Done when:

- Each transformation has at least one focused testcase.
- The written Verilog reflects the modified design.

## Day 4: Verification Hardening

Goals:

- Extend `check_connectivity` beyond missing drivers.
- Add checks for duplicate drivers, undriven primary outputs, floating gate inputs, invalid gate types, and invalid pin counts.
- Add combinational loop detection.
- Add automatic verification after transformations in dispatcher.

Done when:

- Transformation responses include post-check status.
- Bad testcases produce actionable verification messages.

## Day 5: Contest Usability

Goals:

- Improve read/write path handling for contest-style relative paths.
- Make response text concise but informative, close to the examples in the problem statement.
- Add regression inputs for common request sequences.
- Document supported and unsupported operations.

Done when:

- A multi-request testcase can run end to end from stdin.
- Output netlist and log file are generated in expected locations.
- Known unsupported requests fail cleanly instead of crashing.
