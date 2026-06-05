# Current Progress and Next Features

This document summarizes the current implementation direction and the next
features to prioritize after reviewing the contest-style testcase prompts.

## Current Implementation Status

### Basic Operations

Implemented first version:

- `begin_testcase`: reset testcase-local runtime state and create response log.
- `read_design`: load gate-level Verilog through the Yosys-backed parser into
  the project `Design` IR.
- `write_design`: emit the current `Design` IR as normalized primitive Verilog
  and validate it with Yosys before writing.
- Planner safety boundary: natural-language requests are converted into Tool
  API JSON and checked before dispatch.

Current limitation:

- Parser/writer behavior depends on Yosys availability. Windows environments
  may hit Yosys path issues such as `GetShortPathName() failed`.

### Analysis Tasks

Implemented or partially implemented:

- Single path search and avoid-path checks.
- Maximum combinational gate-depth calculation.
- Fanin cone collection.
- Primary-output cone-size report.
- Gate search by type or name substring.
- Same-clock-domain structural DFF check.
- Connectivity, fanout-bound, and depth-bound checks.

Important gaps:

- Complete path enumeration, not only one example path.
- Direct fanout reporting and immediate successor reporting as explicit Tool
  API operations.
- Gate-count report by type.
- More complete structural reports, such as primary input/output widths,
  floating inputs, unconnected outputs, cut/articulation points, and
  register-to-register paths.

### Formal Verification

Implemented first version:

- `check_equivalence`: compare a Boolean expression against a target net.
- `check_property`: prove a Boolean property over a combinational cone.
- `check_design_equivalence`: compare two `Design` objects on common primary
  outputs.
- Function-preserving transformations use transactional execution and
  equivalence guards before commit.

Scope decision:

- Equivalence checking remains combinational-only for now.
- DFFs are treated as sequential boundaries.
- Multi-cycle sequential equivalence is intentionally out of scope for the next
  implementation phase.

Important gaps:

- Runtime does not yet store the original loaded design snapshot.
- There is no Tool API operation yet for checking the current design against
  the original loaded netlist.
- There is no pre-transform snapshot query yet for prompts such as proving
  equivalence to the pre-transformation netlist.

### Transformation And Optimization Tasks

Implemented first version:

- `replace_buffers_with_and`: selected function-changing buffer-to-AND rewrite.
- `remove_dangling`: remove logic not contributing to primary outputs.
- `replace_inv_buf_with_inv`: collapse inverter-buffer chains.
- `replace_or_with_nand_not`: rewrite OR gates in a cone using NAND/NOT logic.
- `insert_buffers_for_fanout`: insert buffer trees to satisfy fanout bounds.
- `balance_depth_with_buffers`: pad independent gate-driven endpoints with
  buffer chains to equalize structural depth.
- `optimize_cone`: local cone simplification for redundant internal buffers and
  double inverters.

Important gaps:

- Rename operations for gates and nets.
- Constant propagation.
- Boolean equation derivation.
- Complete technology mapping, such as XOR-to-NAND, XNOR-to-NOR, full NAND/NOT
  remap, and full AND/NOT remap.
- Structural duplicate merge and functionally equivalent gate merge.
- Pin reconnect with equivalence guard.

## Testcase Coverage Summary

The 40 testcase prompts are broadly aligned with the current roadmap. They
exercise basic IO, analysis, formal checks, transformations, and optimization.

Mostly covered or partially covered:

- Basic testcase setup, design load, and design write.
- Path existence and avoid-path questions.
- Fanin cone and max-depth questions.
- Combinational signal equivalence questions.
- Fanout optimization with inserted buffers.
- Dangling or unused logic removal.
- Simple local cone optimization.

Not yet covered enough:

- Gate-count report by type.
- Complete enumeration of all paths.
- Direct fanout and immediate successor reports.
- Rename gate and rename net.
- Original-loaded-netlist equivalence checks.
- Constant propagation for gates with tied constants.
- Boolean equation derivation.
- Full technology mapping and resynthesis-style restructuring.
- Cut/articulation analysis.
- Register-to-register path reports and DFF D-pin depth reports.

## Next Feature Priority

### P0: Highest Coverage And Foundation

- `report_gate_counts`
  - Count all gates and DFFs, broken down by gate type.
- `report_fanout`
  - Report direct driven gates or DFF pins for a given net.
- `report_gate_connections`
  - Report gate type, output net, input pins, and immediate successors.
- `rename_net`
  - Implement with a shared `replace_net_references` helper.
  - This should update all gate, DFF, port, and wire references safely.
- `check_equivalent_to_original`
  - Store an original design snapshot after `read_design`.
  - Compare current design against the original snapshot using the existing
    combinational `check_design_equivalence`.

### P1: Common Testcase Requests

- `all_paths`
  - Enumerate all combinational paths between a source and destination with
    practical limits to avoid path explosion.
- `rename_gate`
  - Rename an instance while preserving all connectivity.
- `constant_propagation`
  - Simplify AND/OR/NAND/NOR gates with constant inputs.
- `derive_boolean_equation`
  - Pretty-print a target net's Boolean equation in terms of primary inputs.

### P2: Advanced Transformations And Reports

- `reconnect_gate_input`
  - Try a pin reconnect on a copied design and commit only if connectivity and
    equivalence checks pass.
- Technology mapping
  - XOR-to-NAND, XNOR-to-NOR, full NAND/NOT remap, and full AND/NOT remap.
- Graph-structure analysis
  - Cut/articulation points and structural reachability reports.
- Sequential structural reports
  - Register-to-register paths, DFF D-pin depth, clock/reset fanout reports.
- Duplicate merge
  - Merge structural duplicates first; later consider functionally equivalent
    gate-pair merging with formal guards.

## Implementation Notes

- Prioritize `rename_net` before `pin reconnect`. Safe net-reference update is
  a common foundation for later transformations.
- Keep all function-preserving transformations transactional.
- Continue using connectivity, equivalence, and explicit constraint checks
  before committing transformed designs.
- Keep equivalence combinational-only until there is a clear requirement for
  multi-cycle sequential reasoning.
