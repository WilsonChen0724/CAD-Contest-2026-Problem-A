# Release Testcase TODO Cheat Sheet

This note tracks capabilities observed while running the A_release testcase_0510 suite.
The alpha-test goal is to maximize supported, non-error responses across the 40 release
testcases before widening into lower-priority optimization features.

## Immediate Alpha Checks

- Re-run `test25` after schema/rule exposure for `rename_gate` and confirm gate instance rename no longer becomes `rename_net`.
  - Current local result: `test25` with `--planner rule` has 9 responses, 0 missing, 0 unsupported, 0 errors.
- Re-run the higher-risk late cases with strict logging:
  `test31`, `test32`, `test34`, `test38`, and `test40`.
  - Current local result: `test31` with `--planner rule` has 20 responses, 0 missing, 0 unsupported, 0 errors.
  - Current local observation: `test32` reached 20 supported responses in rule output before the multi-case run timed out on later cases.
- Install `z3-solver` in every teammate's active Python environment so large-design equivalence checks do not fall back to the 12-variable brute-force limit.
- Keep `agent/prompt.txt`, `agent/tool_schema.py`, `agent/plan_checker.py`, and `runtime/dispatcher.py` synchronized whenever a new operation is added.

## Implemented Or Exposed In Current Branch

- Basic IO: `begin_testcase`, `read_design`, `write_design`.
- Core reports: `report_gate_counts`, `report_fanout`, `report_gate_connections`.
- C_M3 reports now exposed to LLM schema: `report_io_counts`, `report_fanout_cone`, `report_constant_input_gates`, `gate_on_max_depth_path`.
- Test31 reports now implemented: `report_shared_fanin_cone_gates`, `derive_boolean_equation`, `report_max_depth_to_dff_d`, `report_outputs_depth_greater_than`, `report_all_paths`, and `report_gate_type_count`.
- Sequential boundary reports: `same_clock_domain`, capped `report_register_paths`.
- Transform tools now exposed to LLM schema: `rename_net`, `rename_gate`, `constant_propagation`, `replace_nand_const1_with_not`, `insert_buffers_for_all_high_fanout`, `insert_dedicated_buffers_for_each_load`, `collapse_back_to_back_inverters`, `optimize_design_depth`, `replace_xnor_nor_with_basic_gates`, `replace_and_not_with_nand`, and `merge_equivalent_gates`.
- Verification: connectivity/fanout/depth checks and combinational equivalence to original loaded snapshot.

## P0 Remaining Analysis / Report Tools

- None currently identified after adding `report_all_paths` and `report_gate_type_count`. Re-run LLM release cases to discover any remaining P0 report gaps.

## P1 Remaining Analysis / Report Tools

- `derive_boolean_equation`: derive a Boolean equation for an output or internal signal in terms of primary inputs when tractable.
- Better register-path filtering, for example PI-to-DFF-D or DFF-Q-to-PO if release prompts require those exact scopes.

## P0/P1 Remaining Transform Tools

- Improve or extend `constant_propagation` and rewrite transforms so responses report how many gates were eliminated or inserted.
- Improve transform history/stat queries beyond the current generic before/after delta, for example more domain-specific wording for:
  - how many BUF gates were added by the previous buffer insertion,
  - how many dangling gates were removed,
  - how many NAND gates were eliminated by constant propagation.
- Technology mapping backend integration remains P2; short-term transforms should stay local and guarded by connectivity/equivalence checks.

## Verification / Robustness

- Treat large-design equivalence as Z3-required, or introduce an explicit `inconclusive` result instead of hard failure when only brute force is available.
- Keep the transactional transform guard: transforms should not introduce new connectivity issues, but should tolerate pre-existing release-netlist issues.
- Add regression tests for every new op in dispatcher, plan_checker, planner, and LLM tool schema behavior.

## Documentation Sync

- Update `docs/tool_spec.md` whenever new ops are implemented or newly exposed.
- Update `docs/testing_guide.md` with any new release testcase commands or required environment variables.
- Keep this cheat sheet short and practical; move finished items into release notes or README when they become stable.
