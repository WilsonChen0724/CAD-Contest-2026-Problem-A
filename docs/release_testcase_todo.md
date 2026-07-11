# Release Testcase TODO Cheat Sheet

This note tracks capabilities observed while running the A_release testcase_0510 suite.
The beta-test goal is to maximize supported, non-error responses across the 40 release
testcases, then validate the completed run with the ledger-based external
validator before submission.

## Immediate Beta Checks

- Re-run the higher-risk late cases with strict logging:
  `test31`, `test32`, `test34`, `test35`, `test38`, and `test40`.
- Run at least one official timeout profile before submission:
  `--basic-timeout 60 --timeout 300`.
- Install `z3-solver` in every teammate's active Python environment so large-design equivalence checks do not fall back to the 12-variable brute-force limit.
- Keep `agent/prompt.txt`, `agent/tool_schema.py`, `agent/plan_checker.py`, and `runtime/dispatcher.py` synchronized whenever a new operation is added.

## Beta P0 Team Split

- Person A: parser/writer and graph correctness. Prioritize any testcase that
  fails during `read_design`, `write_design`, net connectivity rebuild, bus
  handling, or DFF/pin parsing.
- Person B: planner, tool schema, and prompt-to-operation mapping. Prioritize
  unsupported responses, wrong tool selection, semantic guard behavior, and
  provider schema synchronization.
- Person C: release runner, validation ledger, transform guards, and external
  correctness metrics. Prioritize runner errors, failed transform validation,
  validator FAIL/INCONCLUSIVE classification, and reportable QoR metrics.

Person C beta P0 commands:

```powershell
python -m unittest discover -s tests
python scripts\run_release_testcases.py --all --planner rule --validation-ledger --fail-on-error --fail-on-unsupported --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner rule --all --output outputs\validator_rule.jsonl --metrics-output outputs\validator_rule_metrics.csv
python scripts\run_release_testcases.py --all --planner llm_openai --validation-ledger --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner llm_openai --all --output outputs\validator_llm_openai.jsonl --metrics-output outputs\validator_llm_openai_metrics.csv
```

Person C completion criteria for beta P0:

- Runner summary has no missing responses, unsupported responses, or runtime
  errors for the target planner run.
- Validator results are exported as JSONL and metrics CSV.
- Every `FAIL` is either fixed or documented with testcase, response id,
  operation, oracle evidence, and suspected owner.
- Every remaining `INCONCLUSIVE` has an explicit reason, such as bounded
  all-path output, large-design equivalence budget, X semantics, or missing
  selected-output scope.
- Generated runner outputs, copied output netlists, and metrics artifacts stay
  out of the source commit unless the team explicitly wants a report snapshot.

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

- Better register-path filtering, for example PI-to-DFF-D or DFF-Q-to-PO if release prompts require those exact scopes.
- Floating/unconnected-net reports and cut/articulation reports remain useful beta candidates.

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
- Keep LLM repair bounded and pre-dispatch only; runtime transform rejection retry remains a beta design item.
- Add regression tests for every new op in dispatcher, plan_checker, planner, and LLM tool schema behavior.

## Documentation Sync

- Update `docs/tool_spec.md` whenever new ops are implemented or newly exposed.
- Update `docs/testing_guide.md` with any new release testcase commands or required environment variables.
- Keep this cheat sheet short and practical; move finished items into release notes or README when they become stable.
