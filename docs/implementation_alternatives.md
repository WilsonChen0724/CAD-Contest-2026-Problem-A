# Implementation Alternatives

This document records alternative implementation directions so future team
members can compare approaches before replacing or extending the current
prototype.

## Topic: `optimize_cone`

Goal: optimize a target cone while preserving functionality and satisfying
hard constraints such as maximum depth.

### Alternative A: Pure Python Local Rewrite

Current default.

Approach:

- Operate directly on the project `Design` IR.
- Apply small, deterministic, function-preserving rewrites.
- Commit only after connectivity, equivalence, and constraint checks pass.

Good candidate rewrites:

- Remove internal one-input buffers.
- Collapse double inverters.
- Propagate constants through AND, OR, NAND, and NOR gates.
- Apply idempotent identities such as `and(a, a) -> a` and `or(a, a) -> a`.
- Remove structural duplicate gates with identical type and inputs.

Advantages:

- Easy to debug and explain.
- Keeps all behavior inside the existing IR and Tool API boundary.
- Good fit for testcase coverage work.
- Does not depend on external optimizer behavior.
- Name preservation is manageable.

Disadvantages:

- Limited optimization power.
- Does not perform Boolean resynthesis, factoring, or technology mapping search.
- May fail to satisfy depth constraints even when a better rewrite exists.

Use this when:

- The requested change is local and structurally obvious.
- The team wants fast testcase coverage with low implementation risk.
- The output must remain easy to inspect.

### Alternative B: Yosys/ABC Backend Resynthesis

Future optional backend.

Approach:

- Export the selected cone or full design to Verilog/Yosys.
- Run Yosys/ABC optimization or mapping passes.
- Parse the optimized result back into the project `Design` IR.
- Commit only after connectivity, equivalence, and constraint checks pass.

Possible flows:

- Yosys simplification for local cleanup.
- ABC area optimization for gate-count reduction.
- ABC depth-oriented mapping for critical-depth reduction.
- Yosys techmap-style conversions for NAND/NOT, NOR/NOT, or AND/NOT targets.

Advantages:

- Much stronger optimization capability.
- Better suited for large cones and full-netlist restructuring.
- Can support technology mapping requests more naturally.

Disadvantages:

- Harder to preserve instance and net names.
- Harder to debug when the generated result is surprising.
- Requires robust export/import and equivalence checking.
- External tool versions may affect output.

Use this when:

- Local rewrites cannot satisfy a depth or mapping request.
- The requested task is full technology mapping or large-cone optimization.
- The team can tolerate name normalization as long as functionality is proven.

### Default Strategy

Short term:

- Continue using Alternative A.
- Add missing local rewrites that directly improve testcase coverage:
  constant propagation, duplicate removal, and simple technology-specific
  decompositions.

Medium term:

- Add Alternative B as an optional backend for larger optimization tasks.
- Keep the project `Design` IR as the canonical state.
- Treat external optimizer output as an untrusted candidate until it passes:
  connectivity check, combinational equivalence check, and requested hard
  constraints.

## Topic: `rename_net` And `pin reconnect`

### Recommended Order

Implement `rename_net` before `reconnect_gate_input`.

Reason:

- `rename_net` requires a safe global net-reference update helper.
- The same helper will be useful for pin reconnect, technology mapping,
  constant propagation, and buffer insertion cleanup.

### `rename_net` Direction

Approach:

- Add `replace_net_references(design, old_net, new_net)`.
- Update ports, wires, gate inputs, gate outputs, and DFF pins.
- Rebuild graph after the update.
- Reject constants and accidental net merges unless explicitly supported later.
- Run as a transactional, function-preserving transform.

### `pin reconnect` Direction

Approach:

- Modify one gate input pin on a candidate design.
- Rebuild graph.
- Commit only if connectivity and equivalence checks pass.
- For positional primitive gates, treat input index 0 as pin A and input index
  1 as pin B for two-input gates.

Risk:

- Pin reconnect usually changes functionality, so most requests should be
  rejected unless the equivalence guard proves the change is safe.

## Equivalence Scope

Current decision:

- Equivalence checks are combinational-only.
- DFFs are treated as boundaries.
- Multi-cycle sequential equivalence is not planned for the next phase.

Reason:

- The testcase prompts ask for internal signal equivalence, current-vs-original
  netlist equivalence, and pre/post transformation equivalence.
- They do not explicitly require multi-cycle state transition equivalence.
- The current combinational formal engine already supports the expected first
  version of these requests.
