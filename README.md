# CADA1070 Alpha - Day1 Skeleton

This repository is the Day1 baseline for the ICCAD Contest Problem A style project:
LLM-assisted netlist exploration and transformation.

## Day1 Goals

By the end of Day1, the project should have:

- A fixed internal representation (IR) for gate-level Verilog netlists.
- A fixed EDA Tool API that the LLM agent is allowed to call.
- A fixed runtime state object for multi-turn testcase execution.
- A fixed stdout/log response format.
- A minimal CLI skeleton that can read requests from stdin and emit `#RESPONSE N ... #END N`.
- A folder structure that allows three people to work in parallel.

## Run Smoke Test

```bash
chmod +x cada1070_alpha
./cada1070_alpha -config config.example.yaml < tests/smoke_input.txt
```

Expected behavior:

- The program reads each line from stdin.
- It prints responses wrapped by `#RESPONSE <id>` and `#END <id>`.
- It creates a log file under `output/logs/` when a testcase begins.

## Main Modules

```text
agent/      LLM planner, prompt, JSON plan parser
eda/        IR, graph, analysis, transform, verification
parser/     Verilog parser
runtime/    state, dispatcher, response formatter, config loader
docs/       fixed specifications
tests/      smoke tests and small netlists
output/     generated logs and output netlists
```

## Day1 Division

- Person A, data-structure background: `eda/design.py`, parser interface, graph interface.
- Person B, ML background: `agent/`, JSON tool schema, prompt.
- Person C, IC contest background: `runtime/`, CLI, logging, transform/verify interface.

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
