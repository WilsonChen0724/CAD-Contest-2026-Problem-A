# Current Progress and Next Features

This document summarizes the current implementation direction and the next
features to prioritize after reviewing the contest-style testcase prompts.

## Current Implementation Status

### Basic Operations

Implemented:

- `begin_testcase`: reset testcase-local runtime state and create response log.
- `read_design`: load gate-level Verilog through the Yosys-backed parser into
  the project `Design` IR.
- `write_design`: emit the current `Design` IR as normalized primitive Verilog
  and validate it with Yosys before writing.
- Planner safety boundary: natural-language requests are converted into Tool
  API JSON and checked before dispatch.
- LLM semantic guard: prompt and plan categories are checked before dispatch;
  high-confidence mismatches share the bounded LLM repair retry budget.

Current limitations:

- Parser/writer behavior depends on Yosys availability. Windows environments
  may hit Yosys path issues such as `GetShortPathName() failed`; the direct
  contest-subset fallback parser handles the common flattened primitive cases.
- The writer emits normalized primitive Verilog and does not preserve original
  comments or formatting.

### Analysis Tasks

Implemented:

- Gate-count report by primitive type.
- Direct fanout reporting for both nets and gate/DFF instances.
- Gate/DFF connection reports with pin connections and output fanout.
- Primary input/output count report.
- Single path search and avoid-path checks.
- Maximum combinational gate-depth calculation.
- Global maximum-depth-path membership check for one gate.
- Fanin cone collection.
- Transitive fanout cone reporting.
- Primary-output cone-size report.
- Register-to-register path reporting with a practical output cap.
- Constant-input gate reporting.
- Gate search by type or name substring.
- Same-clock-domain structural DFF check.
- Connectivity, fanout-bound, and depth-bound checks.
- Shared-fanin cone reports.
- DFF D-pin depth and output-depth threshold reports.

Remaining improvement areas:

- Path enumeration is bounded and may spill to report files for large outputs.
- More complete structural reports, such as floating inputs, unconnected
  outputs, and cut/articulation points, are useful beta candidates.
- Some report wording is still prompt/planner dependent in LLM mode, so
  regression prompts should be expanded as new official examples appear.

### Formal Verification

Implemented:

- `check_equivalence`: compare a Boolean expression against a target net.
- `check_property`: prove a Boolean property over a combinational cone.
- `check_design_equivalence`: compare two `Design` objects on common primary
  outputs.
- `check_equivalent_to_original`: compare the current design against the
  snapshot captured by `read_design`.
- Function-preserving transformations use transactional execution and
  equivalence guards before commit.

Scope decision:

- Equivalence checking remains combinational-only for now.
- DFFs are treated as sequential boundaries.
- Multi-cycle sequential equivalence is intentionally out of scope for the next
  implementation phase.

Remaining improvement area:

- Runtime transform rejection is not retried automatically. We currently retry
  only pre-dispatch LLM plan errors to avoid timeout and state-management risk.

### Transformation And Optimization Tasks

Implemented:

- `rename_net`: safely rename one net and update structural references.
- `rename_gate`: safely rename one gate instance.
- `constant_propagation`: simplify gates with constant or redundant inputs
  under connectivity and equivalence guards.
- `replace_nand_const1_with_not`: local rewrite for 2-input NAND gates with one
  constant-1 input.
- `replace_buffers_with_and`: selected function-changing buffer-to-AND rewrite.
- `remove_dangling`: remove logic not contributing to primary outputs.
- `replace_inv_buf_with_inv`: collapse inverter-buffer chains.
- `replace_or_with_nand_not`: rewrite OR gates in a cone using NAND/NOT logic.
- `insert_buffers_for_fanout`: insert buffer trees to satisfy fanout bounds.
- `balance_depth_with_buffers`: pad independent gate-driven endpoints with
  buffer chains to equalize structural depth.
- `optimize_cone`: local cone simplification for redundant internal buffers and
  double inverters.
- `optimize_design_depth`: Yosys/ABC-backed candidate flow with conservative
  local fallback and guards.
- `replace_xor_with_nand`, `replace_xnor_with_nor`,
  `replace_and_not_with_nand`, and `replace_with_and_not`: technology mapping
  style rewrites for release prompts.
- `merge_equivalent_gates`: structural duplicate merge.
- `reconnect_gate_input`: guarded pin reconnect.

Remaining improvement areas:

- Optimization quality is still conservative and mostly structural/local.
- ABC/Yosys resynthesis should remain optional and guarded by connectivity,
  equivalence, fanout, and depth checks.
- Transform response summaries can be made more domain-specific, such as
  reporting exactly how many buffers, NANDs, or dangling gates changed.

## Testcase Coverage Summary

The 40 testcase prompts are broadly aligned with the current roadmap. They
exercise basic IO, analysis, formal checks, transformations, and optimization.

Mostly covered or partially covered:

- Basic testcase setup, design load, and design write.
- Gate-count report by type.
- Direct fanout and immediate successor reports for nets and gate instances.
- Transitive fanout cone reports.
- Rename net.
- Original-loaded-netlist equivalence checks.
- Path existence and avoid-path questions.
- Fanin cone and max-depth questions.
- Gate-on-maximum-depth-path membership questions.
- Primary input/output count questions.
- Register-to-register structural path reports with a response cap.
- Constant-input NAND reporting and simple NAND-constant-1 rewriting.
- Combinational signal equivalence questions.
- Fanout optimization with inserted buffers.
- Dangling or unused logic removal.
- Simple local cone optimization.

Still worth improving:

- Better LLM prompt-to-tool robustness for unusual wording.
- Faster large-design optimization under strict 60-second response profiles.
- More complete cut/articulation and floating/unconnected structural reports.
- More domain-specific transform statistics after each optimization pass.

## Next Feature Priority

### P0: Submission Stability

- Keep all current release-case regressions passing in both rule and LLM modes.
- Run official timeout checks with `--basic-timeout 60 --timeout 300` before
  beta-style submissions.
- Keep `agent/tool_schema.py`, `agent/plan_checker.py`, rule planner mappings,
  and dispatcher ops synchronized.
- Maintain `z3-solver` in every teammate environment so large equivalence
  checks do not fall back to small brute-force limits.

### Recently Completed P0 Items

- `report_gate_counts`
  - Count all gates and DFFs, broken down by gate type.
- `report_fanout`
  - Report direct driven gates or DFF pins for a given net, gate instance, or
    DFF instance.
- `report_gate_connections`
  - Report gate type, output net, input pins, and immediate successors.
- `rename_net`
  - Implement with a shared `replace_net_references` helper.
  - This should update all gate, DFF, port, and wire references safely.
- `check_equivalent_to_original`
  - Store an original design snapshot after `read_design`.
  - Compare current design against the original snapshot using the existing
    combinational `check_design_equivalence`.
- `report_fanout_cone`
  - Expose transitive reachable-gate reports for prompts such as "gates
    reachable from primary input n0".
- `report_io_counts`
  - Report primary input/output counts.
- `gate_on_max_depth_path`
  - Determine whether a gate lies on a global maximum-depth combinational path.
- `report_register_paths`
  - Report DFF-Q to DFF-D combinational paths with capped output.
- `report_constant_input_gates`
  - Report NAND or other gates with constant inputs.
- `replace_nand_const1_with_not`
  - Rewrite the structural identity `nand(x, 1) -> not(x)`.

### P1: Common Testcase Requests

- Completion is tracked in `docs/p1_completion_matrix.md`; the frozen beta P1
  scope is currently 9 of 10 capabilities complete.
- `constant_propagation`
  - Improve cleanup and summary reporting after propagation.
- LLM semantic guard tuning
  - Expand local classifier tests as new prompt phrasings appear.
- Report polish
  - Add more domain-specific wording for previous-transform deltas.

### P2: Advanced Transformations And Reports

- Technology mapping
  - Improve optimization quality and consider ABC/Yosys candidate generation.
- Graph-structure analysis
  - Cut/articulation points and structural reachability reports.
- Sequential structural reports
  - Clock/reset fanout reports and richer register-path filtering.
- Runtime repair
  - Consider bounded retry after transform rejection only after adding strict
    time/state guards.

## Implementation Notes

- Keep all function-preserving transformations transactional.
- Continue using connectivity, equivalence, and explicit constraint checks
  before committing transformed designs.
- Keep backend ops, `agent/tool_schema.py`, `agent/plan_checker.py`, rule
  planner mappings, prompt examples, and regression tests synchronized.
- Keep equivalence combinational-only until there is a clear requirement for
  multi-cycle sequential reasoning.
