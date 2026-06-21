# Validator FAIL / INCONCLUSIVE Report

Date: 2026-06-21

This document summarizes the current validator findings from the local
`validate result.txt` supplied by Person C, plus the latest P0 validator
policy update.

## Scope

- Source result file: `C:\Users\user\Desktop\validate result.txt`
- Original summary in that file: `PASS=379, FAIL=31, SKIP=0, INCONCLUSIVE=49`
- `test26` is excluded from the FAIL list because it was already re-run and
  confirmed clean:
  - `Validated 8 response(s): PASS=8, FAIL=0, SKIP=0, INCONCLUSIVE=0`

## Latest P0 Validator Update

The validator no longer skips these large-design analysis checks:

- `find_path`
- `all_paths_pass_through`
- `max_depth`
- `report_max_logic_depth`

Reason:

- These are graph reachability or DAG dynamic-programming checks.
- They are not formal equivalence problems.
- They can be validated structurally without enumerating all paths.

The validator still keeps `report_all_paths` bounded for large designs.
Full all-path enumeration can be exponential, so it remains a separate
file-output and bounded-validation problem.

## FAIL Summary

Most FAIL results are real structural failures found after transformation.
The dominant symptoms are:

- missing drivers
- duplicate DFF drivers
- target gate type still remains after a requested replacement

These should be treated as implementation issues in transform/write-back
logic, not as validator false alarms.

| Testcase | Response | Operation | Main issue |
|---|---:|---|---|
| test27 | 4 | `replace_xor_with_nand` | duplicate DFF drivers |
| test27 | 5 | `collapse_back_to_back_inverters` | duplicate DFF drivers |
| test28 | 4 | `replace_with_and_not` | missing driver `n10` |
| test28 | 6 | `collapse_back_to_back_inverters` | missing driver `n10` |
| test28 | 7 | `optimize_design_depth` | missing driver `n10` |
| test29 | 4 | `replace_with_and_not` | duplicate DFF drivers |
| test31 | 5 | `collapse_back_to_back_inverters` | missing drivers + duplicate DFF drivers |
| test31 | 12 | `rename_net` | missing drivers + duplicate DFF drivers |
| test31 | 14 | `insert_dedicated_buffers_for_each_load` | missing drivers + duplicate DFF drivers |
| test32 | 7 | `replace_nand_const1_with_not` | missing drivers |
| test32 | 10 | `rename_net` | missing drivers |
| test32 | 14 | `remove_dangling` | missing drivers |
| test32 | 17 | `constant_propagation` | missing drivers |
| test34 | 4 | `replace_with_and_not` | missing drivers |
| test35 | 4 | `replace_xnor_with_nor` | missing drivers |
| test35 | 10 | `rename_net` | missing drivers |
| test35 | 14 | `collapse_back_to_back_inverters` | missing drivers |
| test36 | 7 | `constant_propagation` | missing drivers |
| test37 | 5 | `optimize_cone` | missing drivers |
| test38 | 4 | `insert_buffers_for_fanout` | missing drivers |
| test38 | 7 | `collapse_back_to_back_inverters` | missing drivers |
| test38 | 9 | `remove_dangling` | missing drivers |
| test38 | 13 | `rename_net` | missing drivers |
| test38 | 16 | `constant_propagation` | missing drivers |
| test39 | 5 | `constant_propagation` | missing drivers |
| test39 | 12 | `insert_dedicated_buffers_for_each_load` | missing drivers + duplicate DFF drivers |
| test39 | 15 | `constant_propagation` | missing drivers |
| test40 | 5 | `replace_nand_const1_with_not` | missing drivers + duplicate DFF drivers |
| test40 | 7 | `collapse_back_to_back_inverters` | missing drivers + duplicate DFF drivers |
| test40 | 16 | `optimize_cone` | missing drivers + duplicate DFF drivers |

## FAIL Root-Cause Buckets

### 1. Missing Drivers

Examples:

- `test28 response 4/6/7`: missing driver `n10`
- `test32 response 7/10/14/17`: missing drivers around `n108[...]`
- `test34 response 4`: many bus-bit missing drivers
- `test38 response 9/13/16`: missing drivers `n40[4]`, `n44[4]`, `n50[4]`

Likely cause:

- Transform code removes, renames, or reconnects a gate/net but does not update
  every downstream reference.
- Bus-bit nets are especially risky because a partial vector rewrite can leave
  individual bits without drivers.

Required fix direction:

- Centralize net reference replacement.
- Rebuild and check graph after each transform.
- Roll back the transform if connectivity fails.

### 2. Duplicate DFF Drivers

Examples:

- `test27 response 4/5`: duplicate drivers on `n25[...]`
- `test29 response 4`: duplicate drivers on `n13[...]`
- `test31 response 5/12/14`: duplicate drivers on `n49[...]`, `n50[...]`, `n51[...]`, `n52[...]`
- `test40 response 5/7/16`: duplicate drivers on large DFF-related buses

Likely cause:

- DFF output nets are being rewritten like ordinary combinational nets.
- A transform may duplicate or reconnect DFF output references without removing
  the old driver.

Required fix direction:

- Treat DFF Q outputs as protected driven nets.
- Before committing a transform, assert each non-constant net has at most one
  driver.
- For rename/reconnect transforms, explicitly distinguish driver-side and
  load-side updates.

### 3. Target Gate Type Still Remains

The latest validator update may reclassify some previous INCONCLUSIVE results
as FAIL when the requested replacement did not happen.

Examples from latest validator behavior:

- `test33 response 5`: `replace_xnor_nor_with_basic_gates`, XNOR gates remain
- `test35 response 18`: `replace_xor_with_nand`, XOR gates remain
- `test39 response 18`: `replace_xor_with_nand`, XOR gates remain

Likely cause:

- Large-design bounded policy skipped the full replacement.
- The response may be non-error, but the requested structural objective was not
  achieved.

Required fix direction:

- If a replacement is skipped, report it as not completed.
- Validator should check residual gate counts for replacement tasks.
- Backend should either complete the replacement or clearly return unsupported /
  bounded partial result.

## INCONCLUSIVE Summary

INCONCLUSIVE does not necessarily mean wrong. It means the validator could not
prove correctness with the current oracle and time policy.

### Fixed by P0

These large-design checks should no longer be skipped:

- `find_path`
- `all_paths_pass_through`
- `max_depth`
- `report_max_logic_depth`

They now use structural graph algorithms even when the design is large.

### Still Valid INCONCLUSIVE

These categories are still expected:

| Category | Examples | Reason |
|---|---|---|
| Full all-path enumeration | `report_all_paths` | Path count can be exponential. Needs report-file + bounded/hash validation. |
| Large full equivalence | `check_equivalent_to_original`, `check_equivalent_to_last_transform_input` | Needs ABC/Yosys CEC with timeout. |
| Large transform with no selected output | some `remove_dangling`, `rename_gate`, `merge_equivalent_gates` | Connectivity passed, but full equivalence was not proven. |
| Boolean expression on huge cones | `derive_boolean_equation` | Full expression may be too large to inline; needs file output and expression equivalence check. |
| X/unknown semantics | equivalence failures involving `1'bx` | Need a defined X policy before PASS/FAIL. |
| Cut/direct PI-PO global oracle | `check_cut_signal`, `report_direct_pi_po_paths` | Needs scalable graph-specific oracle; not a CEC problem. |

## Recommended Next Validator Work

### P1: File-Based Full Output Validation

Targets:

- `report_all_paths`
- `derive_boolean_equation`
- large fanout / cone reports

Plan:

- Require complete output to be written to `output/reports/...`.
- Keep stdout short: summary + file path + count/hash.
- Validator reads the report file.
- For all-path reports:
  - validate each listed path is structurally legal
  - validate count when not truncated
  - validate hash/count/prefix when bounded

### P2: ABC/Yosys CEC for Large Transforms

Targets:

- `check_equivalent_to_original`
- `check_equivalent_to_last_transform_input`
- function-preserving transforms
- optimization results

Plan:

- Export before/after snapshots.
- Run ABC/Yosys combinational equivalence check with timeout.
- Verdict mapping:
  - proved equivalent: PASS
  - counterexample / mismatch: FAIL
  - timeout / unsupported X semantics: INCONCLUSIVE

### P3: Safer Transform Commit Policy

Before any transform result is accepted:

1. Parse the output netlist.
2. Run connectivity check.
3. Run operation-specific residual check.
4. Run selected-output or full equivalence when available.
5. Commit only if guards pass; otherwise roll back and report rejection.

