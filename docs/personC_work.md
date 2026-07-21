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

Date: 2026-07-20.

Completed 40-case LLM/OpenAI run:

- Runner produced 459 responses across 40 stdout files.
- Response and `#END` counts match; no runtime `Error:` or `Unsupported:`
  markers were found.
- Initial offline validator result was `PASS=439`, `FAIL=2`, `SKIP=0`,
  `INCONCLUSIVE=18`.
- Metrics covered 68 transform/optimization responses: 16 gate-count
  improvements, 1 depth improvement, and 14 validator-confirmed improvements.

The two initial FAIL results were analyzed and fixed:

- `test32 response 14 [remove_dangling]` was a stale/false formal verdict.
  The before/after snapshots have identical 14,114 non-wire lines and identical
  gate/DFF structures; only 73 unused wire declarations were removed. The
  validator now prefers this exact structural certificate.
- `test40 response 10 [find_gates]` correctly reported no XOR gates as
  `Matched gates: none.` The validator now accepts this canonical zero-result
  wording instead of requiring `Matched gates: 0`.

Additional P0 fixes from the same validation pass:

- Removed the obsolete large-design skip for required AND/NOT-to-NAND remap.
  On the test40 snapshot, 1,421 NOT gates were remapped in about one second and
  both AND and NOT residual counts became zero. Full-output Z3 equivalence
  proved all 134 outputs in 17.75 seconds with zero failures.
- Extended AND-to-NAND remapping to arbitrary input counts of two or greater.
- Changed structural duplicate merging to fixed-point passes. The test29
  snapshot now merges 178 gates in four passes with zero mergeable duplicates
  remaining, instead of leaving two newly formed duplicates after one pass.
  Full-output Z3 equivalence proved all 71 outputs in 1.33 seconds.
- Added deterministic validator certificates for instance rename, AND/NOT-only
  reconstruction, constant-1 NAND replacement, NAND-only remap, and structural
  duplicate merging.

After regenerating the test29/test40 ledgers, the measured full-suite result was
`PASS=447`, `FAIL=2`, `SKIP=0`, `INCONCLUSIVE=10`. The two FAIL results were
then resolved as follows:

- `test32 response 14 [remove_dangling]` passes when revalidated with the
  current exact unused-wire certificate. No testcase rerun is required.
- `test33 response 17 [merge_equivalent_gates]` still contained an old bounded
  skip generated before the fixed-point implementation. The large-design skip
  is now removed and the merge is performed in batched graph passes. On its
  83,463-gate snapshot, the dispatcher merged 2,894 gates in 9 passes in 13.95
  seconds, leaving zero structural duplicates and reducing the design to
  80,569 gates.

The projected result after regenerating test33 and exporting the validator
again is `PASS=449`, `FAIL=0`, `SKIP=0`, `INCONCLUSIVE=10`; it remains a
projection until that runner/validator rerun is complete. The remaining
inconclusive classes are bounded all-path
enumeration (4), bounded/optimization evidence (3), targeted buffer formal
scope (1), and unknown/X semantics (2); they must not be relabeled as PASS
without a stronger oracle.

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
