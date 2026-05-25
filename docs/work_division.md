# Work Division and Milestones

This file replaces the original Day1-only division with a milestone-based plan.
The team should still keep ownership boundaries clear so Person A, B, and C can
work in parallel.

## Current Status

M0 skeleton is mostly complete:

- CLI and stdin loop exist.
- Response tags and testcase logs exist.
- Basic `Design`, `Gate`, and `DFF` classes exist.
- MVP parser, writer, graph rebuild, analysis, transform, and verify modules
  exist.
- Planner is currently rule-based and should be replaced or supplemented by an
  LLM planner.

> v0.3.0 note: the main branch has moved beyond this original M0 snapshot.
> The parser/writer path is now Yosys-backed, the LLM planner and plan checker
> exist, max-depth and connectivity checks have been strengthened, and the
> default smoke flow passes when Yosys is available.

Known gaps:

- Parser/writer behavior now depends on Yosys availability in the local or
  evaluation environment.
- The first batch of extra analysis helpers has been exposed through
  `agent/plan_checker.py`, `agent/planner.py`, and `runtime/dispatcher.py`:
  all-paths-through, primary-output cone-size reports, and same-clock-domain
  DFF checks.
- Several transformation tools are still placeholders.
- Combinational equivalence/property checking is now implemented for the first
  Tool API version. It uses `z3-solver` when installed and a small brute-force
  fallback otherwise.
- Optimization tasks are not implemented yet.

## Person A: EDA Core / Parser / Graph

Owner files:

```text
eda/design.py
eda/graph.py
eda/analysis.py
parser/verilog_parser.py
parser/verilog_writer.py
```

Primary responsibilities:

- Extend IR to support contest primitives, buses, constants, and DFFs.
- Implement robust parser/writer support for the contest Verilog subset.
- Add optional parser helper adapters if needed, such as Yosys or Lark.
- Rebuild driver/fanout maps after every parse or transformation.
- Implement graph algorithms for path, avoid-path, all-paths-through, logic
  cone, cone size, and true maximum depth.
- Keep DFFs as sequential boundaries for combinational analysis.

Milestone targets:

- M1: parser, writer, graph, and core analysis tools.
- M2: cone extraction support for formal verification.
- M3: graph support for fanout and depth optimization.

## Person B: LLM Agent / Tool Schema / Prompt

Owner files:

```text
agent/planner.py
agent/llm_api.py
agent/prompt.txt
docs/tool_spec.md
```

Primary responsibilities:

- Maintain `docs/tool_spec.md` as the exact contract exposed to the LLM.
- Replace or supplement the rule-based planner with an LLM JSON planner.
- Ensure the LLM only emits allowed Tool API operations.
- Add JSON/schema validation before dispatch.
- Add one repair pass for invalid LLM output.
- Write prompt examples for PDF-style requests.
- Add planner tests for paraphrases and multi-step requests.

Milestone targets:

- M1: rule-based coverage for basic contest examples.
- M2: LLM planner with strict schema validation.
- M4: robust prompt, repair, and fallback behavior.

## Person C: Runtime / Transformation / Verification

Owner files:

```text
main.py
cada1070_alpha
runtime/state.py
runtime/dispatcher.py
runtime/response.py
runtime/config.py
eda/transform.py
eda/verify.py
```

Primary responsibilities:

- Keep CLI, response formatting, and testcase logging contest-compliant.
- Reset testcase state correctly when `begin_testcase` is called.
- Validate tool calls before execution.
- Make transformations transactional.
- Implement structural checks: connectivity, duplicate drivers, floating nets,
  fanout, and depth.
- Implement transformations: buffer-to-AND, dangling removal, inverter-buffer
  merge, OR-to-NAND/NOT, fanout buffer insertion, and depth balancing.
- Integrate Z3-based equivalence/property checks.
- Optionally experiment with Yosys/ABC optimization adapters.

Milestone targets:

- M1: basic transformations and structural checks.
- M2: formal verification.
- M3: optimization and optional external EDA adapters.
- M4: timeout handling and end-to-end regression.

## Milestone Plan

### M0: Skeleton Stabilization

Done when:

- `./cada1070_alpha -config config.example.yaml < tests/smoke_input.txt` runs.
- stdout uses `#RESPONSE N` and `#END N`.
- testcase log is generated.
- current modules import cleanly.

### M1: Basic Contest Pass

Done when:

- Scalar and bus netlists parse into IR.
- DFFs are represented and block combinational traversal.
- Read/write tools work.
- Path, avoid-path, all-paths-through, max-depth, logic-cone, cone-size, and
  gate-search analysis tools work.
- Basic transformations work and pass connectivity checks.

### M2: Formal Verification

Done when:

- Combinational cones can be encoded into Z3.
- `check_equivalence` and `check_property` return counterexamples when false.
- Function-preserving transformations are checked before commit.

### M3: Optimization

Done when:

- High-fanout buffer insertion satisfies max-fanout bounds.
- Depth balancing inserts a minimal or near-minimal number of buffers.
- Cone optimization satisfies hard constraints before minimizing gate count.
- Optional Yosys/ABC adapters are used only behind verification guards.

### M4: Submission Hardening

Done when:

- LLM planner has schema validation and fallback.
- All PDF-style example requests have regression tests.
- Runtime handles invalid requests, invalid tool calls, and timeouts gracefully.
- No API keys, logs, caches, or generated output are committed.

## Dependency Ownership

Core dependencies:

- Person A: `networkx` integration for graph algorithms.
- Person C: `z3-solver` integration for formal checks.

Optional dependencies:

- Person A: `lark` or Yosys parser normalization.
- Person C: Yosys/ABC transformation or optimization adapters.
- Person B: no external EDA tool dependency; the planner should only see Tool API
  descriptions.
