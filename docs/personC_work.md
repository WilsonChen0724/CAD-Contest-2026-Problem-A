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

## Beta P1 Progress

Date: 2026-07-24.

Completed in `fix/validator-followup`:

- Added exact DAG path counting without enumerating every path. The validator
  can now prove the exact path count for large acyclic cones even when the
  response intentionally lists only a bounded prefix.
- The runtime now expands a default all-path request when the exact count is at
  most 10,000. On the current `test18` snapshot, all 5,402 paths are generated
  in about 0.36 seconds; the testcase must be rerun to replace its old
  5,000-path truncated ledger.
- Added exact compositional certificates for single-net buffer trees and
  whole-design buffer forests. Contracting every added BUF must reconstruct
  the before design, and every resulting driver must satisfy the requested
  fanout bound.
- Added an identity-gate removal certificate for `AND(x,x)`, `OR(x,x)`, and
  BUF contraction. The existing `test28 response 7` ledger now validates
  without a rerun: all 8 responses PASS, and the 23 removed degenerate AND
  gates reconstruct exactly while the whole design remains AND/NOT-only.
- Removed the default 24-net cap from required whole-design fanout repair.
  On `test36 response 14`, all 168 high-fanout roots are repaired with 2,615
  BUF gates in about 28.7 seconds, final maximum fanout is 16, and the buffer
  forest certificate passes. `test36` must be rerun to replace the old partial
  ledger.
- Constrained cone optimization now resolves a DFF-Q output to its D-input
  combinational cone. The plan checker also removes redundant whole-design
  gate-library rewrites after a constrained `optimize_cone`.
- On `test37 response 5`, target `n8` resolves to D input `n1168`; only its
  three-gate cone is transformed, rather than reconstructing the entire
  46,979-gate design. Z3 proves equivalence of `n1168` in about 2.2 seconds,
  and the residual cone contains only NAND/NOT gates. `test37` must be rerun.

Current bounded result that should remain honest:

- `test14` has 289,366 and 203,810 exact paths for its two all-path requests.
  Exact counts are now proven, but fully listing those paths would produce
  excessive output. These responses remain `INCONCLUSIVE` under the current
  output-bound policy unless the official checker accepts a report file plus
  exact-count certificate as complete fulfillment.

Latest targeted rerun result:

- `test18`, `test36`, and `test37` produced 52 responses.
- Validator result: `PASS=52`, `FAIL=0`, `SKIP=0`, `INCONCLUSIVE=0`.
- `test18 response 6` now validates the complete 5,402-path artifact line by
  line against deterministic re-enumeration.
- `test37 response 15` now validates the `floating_signals` placeholder by
  recomputing missing and duplicate drivers with the connectivity oracle.

Full existing-ledger audit after the P1 commit:

- 459 responses: `PASS=453`, `FAIL=4`, `INCONCLUSIVE=2`.
- The two inconclusive responses are the known `test14` complete-path requests
  with 289,366 and 203,810 exact paths.
- `test17 response 15` exposed an ABC input-alignment false positive. ABC
  expression miter inputs now use the union of both variable sets, and a Z3
  counterexample overrides an ABC equivalent verdict. The real snapshot now
  reports `n2122 != n2116` with a three-input counterexample.
- `test26 response 4` and `test33 responses 9/19` are old empty-DFF-cone
  ledgers. New dry runs resolve their D-input cones and satisfy the requested
  NOR/NOT or NAND/NOT constraints. `test26` takes about 1 second; the two
  80k-gate `test33` dry runs take about 73 and 103 seconds, both under the
  300-second request limit and both pass selected-D-input equivalence.
- Regenerate `test17`, `test26`, and `test33` with an API-configured shell
  before publishing a new full-suite validator total.

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
