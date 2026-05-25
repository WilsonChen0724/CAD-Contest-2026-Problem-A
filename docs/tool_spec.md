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
