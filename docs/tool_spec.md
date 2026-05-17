# Tool API Specification

The LLM agent may only request operations listed in this document.

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

## 1. Testcase and IO Tools

### begin_testcase

Initialize a new testcase.

Input:

```json
{
  "op": "begin_testcase",
  "args": {
    "case_name": "test8"
  }
}
```

Output summary:

```json
{
  "ok": true,
  "case_name": "test8",
  "message": "Initialized testcase."
}
```

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

Output summary:

```json
{
  "ok": true,
  "module": "top",
  "num_gates": 120,
  "num_dffs": 10
}
```

### write_design

Write current design state to a Verilog file.

Input:

```json
{
  "op": "write_design",
  "args": {
    "path": "test8_out.v"
  }
}
```

Output summary:

```json
{
  "ok": true,
  "path": "test8_out.v"
}
```

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

Notes:

- `avoid` is optional.
- The path should not cross DFF boundaries unless explicitly enabled in future versions.

Output summary:

```json
{
  "ok": true,
  "path": ["in0", "U1", "n1", "U2", "out3"]
}
```

### all_paths_pass_through

Check whether every path from `src` to `dst` passes through `node`.

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

Implementation idea:

Temporarily block `node`, then test whether `src` can still reach `dst`.

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

### logic_cone

Return the transitive fanin cone of an output or internal net.

Input:

```json
{
  "op": "logic_cone",
  "args": {
    "target": "flag"
  }
}
```

Output summary:

```json
{
  "ok": true,
  "target": "flag",
  "gates": ["U1", "U2", "U3"],
  "num_gates": 3
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

## 3. Transformation Tools

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
and U_repl(out, in, extra_input);
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
