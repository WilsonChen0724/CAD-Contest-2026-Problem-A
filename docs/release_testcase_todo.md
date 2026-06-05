# Release Testcase TODO Cheat Sheet

This note tracks missing capabilities observed while running the A_release testcase_0510 suite with `llm_both`.

## Immediate Checks

- Re-run `test25` after adding `rename_gate` and confirm gate instance rename no longer becomes `rename_net`.
- Install `z3-solver` in every teammate's active Python environment so large-design equivalence checks do not fall back to the 12-variable brute-force limit.
- Keep `agent/prompt.txt`, `agent/tool_schema.py`, `agent/plan_checker.py`, and `runtime/dispatcher.py` synchronized whenever a new operation is added.

## Missing Analysis / Report Tools

- `report_primary_io_counts`: report the number of primary inputs and primary outputs.
- `report_transitive_fanout`: list all gates reachable from a source net or primary input.
- `report_shared_fanin_cone_gates`: report gates shared by the fanin cones of two targets.
- `derive_boolean_equation`: derive a Boolean equation for an output or internal signal in terms of primary inputs when tractable.
- `report_all_paths`: list all combinational paths between a source and destination, not only one path.
- `report_max_depth_to_dff_d`: compute maximum logic depth from any primary input to any DFF D pin.
- `report_outputs_depth_greater_than`: count or list outputs whose logic depth is greater than a threshold.
- `check_gate_on_max_depth_path`: determine whether a gate lies on any maximum-depth path of the design.
- `report_nand_gates_with_constant_inputs`: report NAND gates with constant 0/1 inputs.
- `report_gate_type_count`: answer direct count questions such as current NOT gate count.

## Missing Transform Tools

- `insert_dedicated_buffers_for_each_load(net)`: insert one BUF per load so each load is driven through a dedicated buffer.
- `replace_nand_const1_with_not`: replace two-input NAND gates with one input tied to `1'b1` by equivalent NOT gates.
- Improve or extend `constant_propagation` so it reports how many NAND gates were eliminated.
- Add transform history/stat queries, for example:
  - how many BUF gates were added by the previous buffer insertion,
  - how many dangling gates were removed,
  - how many NAND gates were eliminated by constant propagation.

## Verification / Robustness

- Treat large-design equivalence as Z3-required, or introduce an explicit `inconclusive` result instead of hard failure when only brute force is available.
- Keep the transactional transform guard: transforms should not introduce new connectivity issues, but should tolerate pre-existing release-netlist issues.
- Add regression tests for every new op in dispatcher, plan_checker, and LLM tool schema behavior.

## Documentation Sync

- Update `docs/tool_spec.md` whenever new ops are implemented.
- Update `docs/testing_guide.md` with any new release testcase commands or required environment variables.
- Keep this cheat sheet short and practical; move finished items into release notes or README when they become stable.
