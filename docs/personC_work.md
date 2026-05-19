# Person C Work Plan

Owner scope:

- Runtime integration
- Testcase state and response/log handling
- Transformation interface
- Verification interface

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

