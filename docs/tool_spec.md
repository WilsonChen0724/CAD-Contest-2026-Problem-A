# Tool API Specification

The LLM agent may only request operations listed in this document. The backend
owns all parsing, graph analysis, transformation, optimization, and verification.

The planner should output JSON with this shape:

```json
{
  "steps": [
    {
      "op": "tool_name",
      "args": {}
    }
  ]
}
```

For a single-step task, this is also accepted:

```json
{
  "op": "tool_name",
  "args": {}
}
```

Each step may include `save_as` when the result should be stored in
`state.previous_results` for a later request.

## 1. Testcase and IO Tools

### begin_testcase

Initialize a new testcase and reset testcase-local state.

Input:

```json
{
  "op": "begin_testcase",
  "args": {
    "case_name": "test8"
  }
}
```

Effects:

- Reset current design state.
- Clear saved previous results.
- Reset response numbering for the new testcase.
- Create `output/logs/<case_name>.log`.

### read_design

Read a gate-level Verilog design into the current design state.

Input:

```json
{
  "op": "read_design",
  "args": {
    "path": "design/netlist/test8.v"
  }
}
```

Backend rules:

- Parse one flattened top module.
- Support primitive gates, DFFs, wires, buses, and constants.
- Rebuild driver and fanout maps after parsing.
- Optional parser helpers such as Yosys or Lark may be used internally.

### write_design

Write current design state to a gate-level Verilog file.

Input:

```json
{
  "op": "write_design",
  "args": {
    "path": "test8_out.v"
  }
}
```

Backend rules:

- Output legal flattened gate-level Verilog.
- Preserve the current transformed design state.
- Create parent directories when needed.

## 2. Analysis Tools

### find_path

Find one combinational path from `src` to `dst`.

Input:

```json
{
  "op": "find_path",
  "args": {
    "src": "in0",
    "dst": "out3",
    "avoid": []
  }
}
```

Rules:

- `avoid` is optional.
- The path must not cross DFF boundaries.
- `avoid` may contain gate names or net names.
- Return a counterexample path when answering a negative all-paths query.

### all_paths_pass_through

Check whether every combinational path from `src` to `dst` passes through
`node`.

Input:

```json
{
  "op": "all_paths_pass_through",
  "args": {
    "src": "in0",
    "dst": "out3",
    "node": "U7"
  }
}
```

Backend rule:

- Temporarily block `node`.
- If `dst` is still reachable from `src`, return `ok: false` and one path that
  avoids `node`.
- Otherwise return `ok: true`.

### report_all_paths

List bounded combinational paths from `src` to `dst`.

Input:

```json
{
  "op": "report_all_paths",
  "args": {
    "src": "in0",
    "dst": "out3",
    "max_paths": 200
  }
}
```

Rules:

- `max_paths` is optional and defaults to 200.
- The path enumeration must not cross DFF boundaries.
- If the bound is reached, report that enumeration was truncated.

### max_depth

Compute maximum combinational gate depth from `src` to `dst`.

Input:

```json
{
  "op": "max_depth",
  "args": {
    "src": "in0",
    "dst": "out3"
  }
}
```

Depth convention:

- Primary input to primary output with no gate has depth 0.
- Each primitive gate counts as 1.
- DFF is a sequential boundary and is not counted in combinational depth.
- The result should include one longest path when available.

### report_cone_depth

Report the maximum structural depth inside one target fanin cone.

Input:

```json
{
  "op": "report_cone_depth",
  "args": {
    "target": "n14"
  }
}
```

Rules:

- Sources are primary inputs, DFF Q pins, constants, and undriven boundary nets.
- Each primitive gate in the cone counts as 1 level.
- Output includes the cone depth, cone gate count, and one example longest path.

### logic_cone

Return the transitive fanin cone of an output or internal net.

Input:

```json
{
  "op": "logic_cone",
  "args": {
    "target": "flag"
  },
  "save_as": "flag_cone"
}
```

Output should include gate names and the number of gates in the cone.

### report_gate_counts

Report total primitive instance counts by gate type.

Input:

```json
{
  "op": "report_gate_counts",
  "args": {}
}
```

Output includes AND/OR/NOT/NAND/NOR/XOR/XNOR/BUF/DFF counts and total gates.

### report_gate_type_count

Report the current count for one primitive gate type or DFF cell type.

Input:

```json
{
  "op": "report_gate_type_count",
  "args": {
    "gate_type": "not"
  }
}
```

Rules:

- `gate_type` should be one of `and`, `or`, `not`, `nand`, `nor`, `xor`, `xnor`, `buf`, or `dff`.
- Use this for direct questions like current NOT gate count.

### report_fanout

Report direct loads driven by one net.

Input:

```json
{
  "op": "report_fanout",
  "args": {
    "net": "n1"
  }
}
```

Output includes load count, unique sink count, gate/DFF/primary-output sinks,
and consumed input pins when applicable.

### report_fanout_cone

Report the transitive combinational fanout cone from a source net.

Input:

```json
{
  "op": "report_fanout_cone",
  "args": {
    "source": "n0"
  }
}
```

Rules:

- Traverse through primitive combinational gates.
- Stop at DFF input pins and primary outputs.
- Return reachable gates, reached nets, primary-output endpoints, and DFF sinks.

### report_primary_outputs

List primary output names grouped with inferred bit widths.

Input:

```json
{
  "op": "report_primary_outputs",
  "args": {}
}
```

Rules:

- Scalar outputs are reported as 1 bit.
- Expanded bus bits such as `out[0]` and `out[1]` are grouped into one bus width.

### report_gate_connections

Report one gate or DFF instance's type, pin connections, and output fanout.

Input:

```json
{
  "op": "report_gate_connections",
  "args": {
    "gate": "U0"
  }
}
```

### report_outputs_by_cone_size

Report primary outputs whose logic cone contains more than a threshold number of
gates.

Input:

```json
{
  "op": "report_outputs_by_cone_size",
  "args": {
    "min_gates": 100
  }
}
```

### report_largest_fanin_cone_output

Report the primary output or outputs with the largest structural fanin cone by
gate count.

Input:

```json
{
  "op": "report_largest_fanin_cone_output",
  "args": {}
}
```

### report_io_counts

Report the number of primary inputs and primary outputs.

Input:

```json
{
  "op": "report_io_counts",
  "args": {}
}
```

### gate_on_max_depth_path

Check whether one gate lies on any maximum-depth combinational path in the
design.

Input:

```json
{
  "op": "gate_on_max_depth_path",
  "args": {
    "gate": "g0"
  }
}
```

Rules:

- Sources are primary inputs and DFF Q pins.
- Endpoints are primary outputs and DFF D pins.
- DFFs are still treated as sequential boundaries.

### report_shared_fanin_cone_gates

Report gates shared by two transitive fanin cones.

Input:

```json
{
  "op": "report_shared_fanin_cone_gates",
  "args": {
    "target_a": "n16",
    "target_b": "n17"
  }
}
```

### derive_boolean_equation

Derive a structural Boolean equation for a target when tractable.

Input:

```json
{
  "op": "derive_boolean_equation",
  "args": {
    "target": "n16"
  }
}
```

Rules:

- Primary inputs and DFF Q pins are symbolic boundaries.
- Large expressions may be truncated.

### report_max_depth_to_dff_d

Report the maximum combinational depth from any primary input to any DFF D pin.

Input:

```json
{
  "op": "report_max_depth_to_dff_d",
  "args": {}
}
```

### report_max_register_to_register_depth

Report the maximum combinational depth from any DFF Q pin to any downstream DFF
D pin.

Input:

```json
{
  "op": "report_max_register_to_register_depth",
  "args": {}
}
```

Rules:

- DFF Q pins and primary inputs are depth-0 boundaries for the combinational
  logic feeding a destination D pin.
- The reported source DFF is the register whose Q pin starts the example path.

### report_outputs_depth_greater_than

Report primary outputs whose structural logic depth is greater than a threshold.

Input:

```json
{
  "op": "report_outputs_depth_greater_than",
  "args": {
    "min_depth": 4
  }
}
```

### report_last_transform_stats

Report stats from the previous successful transform.

Input:

```json
{
  "op": "report_last_transform_stats",
  "args": {}
}
```

### report_register_paths

Report register-to-register paths through combinational logic.

Input:

```json
{
  "op": "report_register_paths",
  "args": {
    "max_paths": 200
  }
}
```

Rules:

- `max_paths` is optional.
- Traversal starts at DFF Q pins and stops at downstream DFF D pins.
- Output may be capped to avoid path explosion on large designs.

### report_dff_input_logic_structures

Report DFF D-input logic that structurally resembles enable or hold logic.

Input:

```json
{
  "op": "report_dff_input_logic_structures",
  "args": {
    "max_items": 200
  }
}
```

Rules:

- `max_items` is optional.
- The current implementation is structural and heuristic.
- It flags direct AND gating and mux-like OR-of-two-AND structures, including
  hold-like cases where one data term is the DFF Q net.

### report_constant_input_gates

Report gates with one or more constant inputs.

Input:

```json
{
  "op": "report_constant_input_gates",
  "args": {
    "gate_type": "nand"
  }
}
```

Rules:

- `gate_type` is optional.
- Constants currently include `1'b0`, `1'b1`, `0`, and `1`.

### find_gates

Find gates by type and/or name pattern.

Input:

```json
{
  "op": "find_gates",
  "args": {
    "gate_type": "buf",
    "name_contains": "_gc__"
  },
  "save_as": "found_buffers"
}
```

Rules:

- `gate_type` may be null.
- `name_contains` may be null.
- If `save_as` exists, result is stored in `state.previous_results[save_as]`.

### same_clock_domain

Check whether two DFFs are under the same clock domain.

Input:

```json
{
  "op": "same_clock_domain",
  "args": {
    "dff_a": "dff1",
    "dff_b": "dff2"
  }
}
```

## 3. Transformation Tools

Transformations must be transactional. A failed transformation must not modify
the current design state.

### replace_buffers_with_and

Replace selected buffers with 2-input AND gates.

Input:

```json
{
  "op": "replace_buffers_with_and",
  "args": {
    "targets": ["U_gc__buf0", "U23_gc__buf"],
    "extra_input": "_gc_ctrl"
  }
}
```

If `targets` is omitted, planner may use:

```json
{
  "op": "replace_buffers_with_and",
  "args": {
    "targets_from": "found_buffers",
    "extra_input": "_gc_ctrl"
  }
}
```

Rewrite:

```verilog
buf U(out, in);
```

becomes:

```verilog
and U(out, in, extra_input);
```

### remove_dangling

Remove gates and nets that do not affect any primary output.

Input:

```json
{
  "op": "remove_dangling",
  "args": {}
}
```

### replace_inv_buf_with_inv

Replace inverter followed by buffer with a single inverter when safe.

Input:

```json
{
  "op": "replace_inv_buf_with_inv",
  "args": {}
}
```

Safety condition:

- Intermediate net must have only the buffer as fanout.
- The replacement must preserve the final output net value.

### collapse_back_to_back_inverters

Collapse safe NOT followed by NOT chains into direct wiring.

Input:

```json
{
  "op": "collapse_back_to_back_inverters",
  "args": {}
}
```

Rules:

- The intermediate net between the two inverters must have only the second
  inverter as fanout.
- If the second inverter drives a primary output net, it is rewritten as a BUF
  to preserve the output driver name.

### replace_or_with_nand_not

Replace 2-input OR gates in a cone with NAND/NOT equivalent logic.

Input:

```json
{
  "op": "replace_or_with_nand_not",
  "args": {
    "cone_target": "flag"
  }
}
```

Rewrite:

```verilog
or U(y, a, b);
```

becomes:

```verilog
not U_na(na, a);
not U_nb(nb, b);
nand U_nand(y, na, nb);
```

### replace_nand_const1_with_not

Replace 2-input NAND gates that have one constant-1 input with inverters.

Input:

```json
{
  "op": "replace_nand_const1_with_not",
  "args": {}
}
```

Rewrite:

```verilog
nand U(y, a, 1'b1);
```

becomes:

```verilog
not U(y, a);
```

Rules:

- Only rewrites 2-input NAND gates with exactly one non-constant data input and
  one constant-1 input.
- The output net and instance name are preserved.
- This local identity is function-preserving; transactional connectivity guards
  must not allow new connectivity regressions.

### insert_buffers_for_fanout

Insert buffer stages on a high-fanout net so every driven gate fanout is at most
`max_fanout`.

Input:

```json
{
  "op": "insert_buffers_for_fanout",
  "args": {
    "net": "clk_en",
    "max_fanout": 8
  }
}
```

Rules:

- Preserve logical functionality.
- Report number of inserted buffers and final maximum fanout.
- Commit only after connectivity, equivalence, and fanout-bound checks pass.

### insert_dedicated_buffers_for_each_load

Insert one dedicated BUF per current direct load of a net or signal.

Input:

```json
{
  "op": "insert_dedicated_buffers_for_each_load",
  "args": {
    "net": "n2"
  }
}
```

Rules:

- Gate and DFF input sinks are redirected through dedicated buffer output nets.
- Primary-output sinks are left direct in the current implementation.
- Commit only after connectivity and combinational equivalence checks pass.

### balance_depth_with_buffers

Add buffers to balance path depths from one source to multiple destinations.

Input:

```json
{
  "op": "balance_depth_with_buffers",
  "args": {
    "src": "A",
    "dsts": ["B", "C", "D", "E"],
    "minimize_buffers": true
  }
}
```

Rules:

- Only insert buffers.
- Preserve functionality.
- Prefer the fewest inserted buffers that satisfy the requested balance.
- First implementation supports independent gate-driven destination nets.
- Commit only after connectivity, equivalence, and final depth-balance checks
  pass.
- This is a structural logic-depth operation. It treats primitive gates,
  including buffers, as depth stages and does not model physical timing delay.

### optimize_cone

Optimize the logic cone of a target under hard constraints.

Input:

```json
{
  "op": "optimize_cone",
  "args": {
    "target": "h",
    "max_depth": 5,
    "minimize_gate_count": true
  }
}
```

Rules:

- Hard constraints must be satisfied first.
- Gate count minimization is secondary.
- Optional Yosys or ABC adapters may be used internally.
- Equivalence must be checked before commit when functionality must be preserved.
- First implementation performs local simplification only: redundant internal
  buffer removal and double-inverter simplification.

### optimize_design_depth

Run full-design structural depth optimization using Yosys/ABC when available.
If Yosys/ABC cannot run, the backend falls back to conservative local cone
cleanup so the command remains safe to execute in restricted environments.

Input:

```json
{
  "op": "optimize_design_depth",
  "args": {
    "cost_function": "max_logic_depth",
    "objective": "minimize",
    "cost_scope": "whole_design",
    "max_depth": 5
  }
}
```

Arguments:

- `cost_function` should be `max_logic_depth` for final-design maximum logic
  depth optimization.
- `objective` should be `minimize` when smaller cost is better.
- `cost_scope` should be `whole_design` when optimizing the final design cost.
- `max_depth` is optional. When provided, the backend asks ABC to optimize toward
  that delay/depth target and reports whether the final structural depth met it.

Rules:

- Treat DFF Q pins and primary inputs as combinational sources.
- Treat DFF D pins and primary outputs as combinational sinks.
- Preserve DFF boundaries while optimizing combinational logic between them.
- Return initial/final gate counts, initial/final depth, selected engine, and
  whether the requested target depth was met.
- The dispatcher runs this as a transactional transform; failed runs do not
  modify the current design state.

### rename_net

Safely rename one net by updating declarations and all structural references.

Input:

```json
{
  "op": "rename_net",
  "args": {
    "old_net": "n_mid",
    "new_net": "renamed_mid"
  }
}
```

Rules:

- Reject constants, missing nets, existing net-name collisions, and instance-name
  collisions.
- Update PI/PO/wire declarations, gate inputs/outputs, and DFF D/Q/CLK/RST pins.
- Commit only after connectivity and combinational equivalence checks pass.
- This is a net rename operation, not a net merge or arbitrary pin reconnect.

### constant_propagation

Simplify gates with constant or redundant inputs while preserving functional
equivalence.

Input:

```json
{
  "op": "constant_propagation",
  "args": {}
}
```

Current supported simplifications:

- AND/OR/NAND/NOR with `1'b0`, `1'b1`, `0`, or `1` inputs.
- NOT/BUF driven by constants.
- Duplicate inputs for idempotent gates, such as `and(a, a) -> a`.
- XOR/XNOR constant folding and duplicate-pair cancellation.

Rules:

- Commit only after connectivity and combinational equivalence checks pass.
- Internal simplified outputs may be reconnected to the replacement net or
  constant.
- Primary-output drivers are preserved by rewriting the driving gate to a
  `buf` or `not` when needed.

## 4. Verification Tools

### check_connectivity

Check drivers, fanouts, floating nets, and duplicate drivers.

Input:

```json
{
  "op": "check_connectivity",
  "args": {}
}
```

### check_fanout

Check maximum fanout bound.

Input:

```json
{
  "op": "check_fanout",
  "args": {
    "max_fanout": 8
  }
}
```

### check_depth

Check maximum depth bound.

Input:

```json
{
  "op": "check_depth",
  "args": {
    "src": "in0",
    "dst": "out3",
    "max_depth": 5
  }
}
```

### check_equivalent_to_original

Check whether the current design is equivalent to the original loaded netlist
snapshot captured by `read_design`.

Input:

```json
{
  "op": "check_equivalent_to_original",
  "args": {}
}
```

Rules:

- Scope is combinational-only.
- DFFs are treated as boundaries.
- Multi-cycle sequential equivalence is intentionally out of scope for now.

### check_equivalent_to_last_transform_input

Check whether the current design is equivalent to the design state immediately
before the previous successful transform.

Input:

```json
{
  "op": "check_equivalent_to_last_transform_input",
  "args": {}
}
```

Rules:

- Scope is combinational-only.
- DFFs are treated as boundaries.

### check_equivalence

Check whether a Boolean expression is equivalent to a target net.

Input:

```json
{
  "op": "check_equivalence",
  "args": {
    "expr": "a & b",
    "target": "z"
  }
}
```

Rules:

- Encode the target combinational cone.
- Use a Boolean solver such as Z3 when available.
- Return a counterexample assignment when not equivalent.

### check_property

Check whether a target signal satisfies a Boolean property.

Input:

```json
{
  "op": "check_property",
  "args": {
    "target": "done",
    "property": "done -> (req & !busy)"
  }
}
```

Rules:

- Encode the relevant combinational cone.
- Return `ok: true` if the property always holds.
- Return `ok: false` with a counterexample if it does not hold.

## 5. Planner Output Examples

### Example 1

Request:

```text
What is the maximum logic depth from input in0 to output out3?
```

Planner output:

```json
{
  "op": "max_depth",
  "args": {
    "src": "in0",
    "dst": "out3"
  }
}
```

### Example 2

Request:

```text
Find all the buffers whose name includes _gc__.
```

Planner output:

```json
{
  "op": "find_gates",
  "args": {
    "gate_type": "buf",
    "name_contains": "_gc__"
  },
  "save_as": "found_buffers"
}
```

### Example 3

Request:

```text
Replace the found buffers with AND gates and connect the other input to _gc_ctrl.
```

Planner output:

```json
{
  "op": "replace_buffers_with_and",
  "args": {
    "targets_from": "found_buffers",
    "extra_input": "_gc_ctrl"
  }
}
```

### Example 4

Request:

```text
Does every path from A to B pass through C?
```

Planner output:

```json
{
  "op": "all_paths_pass_through",
  "args": {
    "src": "A",
    "dst": "B",
    "node": "C"
  }
}
```

### Example 5

Request:

```text
Optimize the logic cone of h so that the maximum depth is less than or equal to 5 and the gate count is minimized.
```

Planner output:

```json
{
  "op": "optimize_cone",
  "args": {
    "target": "h",
    "max_depth": 5,
    "minimize_gate_count": true
  }
}
```
