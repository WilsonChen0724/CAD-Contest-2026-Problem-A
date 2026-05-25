# CADA1070 Alpha

This repository is an alpha baseline for an ICCAD Contest Problem A style
project: LLM-assisted netlist exploration and transformation.

The system accepts natural-language requests, translates them into a restricted
Tool API plan, executes deterministic EDA backend operations on the current
gate-level Verilog design state, and prints contest-compliant responses.

The LLM must not directly edit Verilog. It may only call tools described in
`docs/tool_spec.md`.

## Architecture

```text
stdin request
    |
    v
runtime/main loop
    |
    v
agent/planner.py
    |
    v
validated Tool API JSON plan
    |
    v
runtime/dispatcher.py
    |
    v
eda backend + optional open-source helper adapters
    |
    v
stdout response + testcase log + optional output netlist
```

## Open-Source Helper Policy

Open-source tools may be used behind deterministic adapters. The canonical
design state remains the project `Design` IR.

Recommended core helpers:

- `networkx`: graph traversal, path queries, cone analysis, depth computation.
- `z3-solver`: equivalence checks, property checks, counterexamples.

Optional helpers:

- `lark`: parser for the contest Verilog subset.
- `yosys`: Verilog normalization and optional formal/sanity checks.
- `abc`: cone-level logic optimization experiments.

Reference-only or lower-priority helpers:

- `circuitgraph`
- `pyverilog`

The pure-Python backend should still cover core contest tasks if optional
external binaries are unavailable.

## Main Modules

```text
agent/      LLM planner, prompt, JSON plan parser
eda/        IR, graph, analysis, transform, verification
parser/     Verilog parser and writer
runtime/    state, dispatcher, response formatter, config loader
docs/       system spec, tool spec, team division
tests/      smoke tests and small netlists
output/     generated logs and output netlists
```

## Run Smoke Test

```bash
chmod +x cada1070_alpha
./cada1070_alpha -config config.example.yaml < tests/smoke_input.txt
```

Expected behavior:

- The program reads each line from stdin.
- It prints responses wrapped by `#RESPONSE <id>` and `#END <id>`.
- It creates a log file under `output/logs/` when a testcase begins.
- Later requests operate on the current testcase design state.

## Milestones

### M0: Skeleton Stabilization

- Repository structure exists.
- CLI reads stdin and emits valid response blocks.
- Testcase logging works.
- MVP parser, writer, dispatcher, and smoke test exist.

### M1: Basic Contest Pass

- Scalar and bus netlists parse into IR.
- DFFs are represented as sequential boundaries.
- Read/write, path, avoid-path, all-paths-through, max-depth, logic-cone, and
  gate-search tools work.
- Basic transformations pass structural checks.

### M2: Formal Verification

- Z3-based combinational cone encoding works.
- Equivalence and property checks are supported.
- Function-preserving transformations verify before commit.

### M3: Optimization

- Fanout buffer insertion satisfies hard fanout bounds.
- Depth balancing supports buffer insertion.
- Cone optimization reduces gate count under hard constraints.
- Optional Yosys/ABC adapters may be used behind verification guards.

### M4: Submission Hardening

- LLM planner supports schema validation and one repair pass.
- Runtime handles invalid requests and failed transformations gracefully.
- Regression tests cover PDF-style examples.
- Config files do not expose API keys.

## Team Division

- Person A, data-structure background: IR, parser, writer, graph, analysis.
- Person B, ML background: LLM planner, JSON schema, prompt, tool spec.
- Person C, IC contest background: runtime, dispatcher, transformations,
  verification, optional optimization adapters.

See `docs/work_division.md` for the detailed milestone-based ownership
plan.

## Version Notes

### v0.2.0 - Day2 correctness fixes (2026-05-20)

This version records five small but important correctness fixes found after the
Day2 LLM planner integration.

1. `cada1070_alpha` line-ending and executable-mode handling

   Added `.gitattributes` rules so shell scripts and source files keep stable LF
   line endings across Windows/Linux environments. The `cada1070_alpha` launcher
   is also intended to be stored with executable permission in Git, so Linux/macOS
   users can run it with:

   ```bash
   ./cada1070_alpha -config config.example.yaml < tests/smoke_input.txt
   ```

2. `unsupported` plan consistency between checker and dispatcher

   The plan checker already allowed:

   ```json
   {"op": "unsupported", "args": {"reason": "..."}}
   ```

   The dispatcher now accepts the same operation and returns a user-facing
   message instead of rejecting a checker-approved plan.

3. Real maximum-depth analysis

   `eda/analysis.py::max_depth()` no longer estimates depth by taking one BFS
   path and counting gates. It now performs longest-depth propagation from the
   source net over the fanout graph:

   - nets carry the best depth reached so far,
   - crossing a primitive gate adds one level,
   - DFF sinks are treated as sequential boundaries and are not crossed,
   - a guard reports likely combinational loops instead of looping forever.

4. Bus and DFF parser support

   The Verilog parser now supports simple bus declarations and expands them into
   bit-select nets in the IR. For example:

   ```verilog
   input [1:0] a;
   ```

   becomes internal nets `a[0]` and `a[1]`.

   It also supports normalized positional DFF syntax:

   ```verilog
   dff FF0(q, d, clk);
   dff FF1(q, d, clk, rst);
   ```

   The writer can emit these bus declarations and DFF instances back to Verilog.
   Full library-specific named-pin cell parsing is still future work.

5. Stronger connectivity verification

   `eda/verify.py::check_connectivity()` now reports duplicate drivers in
   addition to missing drivers. Duplicate drivers include cases where one net is
   driven by multiple gates, by a primary input and a gate, or by a DFF and
   another driver.

   Example result shape:

   ```python
   {
       "ok": False,
       "missing_drivers": [],
       "duplicate_drivers": {
           "y": ["GATE:U1", "GATE:U2"]
       }
   }
   ```

Regression tests were added for unsupported-plan dispatching, maximum-depth
analysis, bus/DFF parsing and writing, and duplicate-driver verification.
