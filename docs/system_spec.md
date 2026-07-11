# System Specification

## 1. Project Goal

Build a deterministic LLM-assisted EDA system for ICCAD Contest Problem A style
netlist exploration and transformation.

The system accepts natural-language requests from stdin, asks an LLM planner to
translate each request into a validated Tool API plan, executes that plan on the
current gate-level Verilog design state, and returns a concise response or writes
a transformed netlist.

The LLM must never directly edit Verilog. It may only request operations listed
in `docs/tool_spec.md`. All parsing, graph analysis, transformation,
optimization, and verification are performed by deterministic backend code.

## 2. High-Level Architecture

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
response formatter
    |
    v
stdout + testcase log + optional output netlist
```

## 3. Open-Source Helper Strategy

Open-source libraries and tools may be used as helpers, but they do not replace
the project's public Tool API or persistent design state.

### 3.1 Dependency Tiers

Core Python helpers:

- `networkx`: graph algorithms for reachability, path queries, cone traversal,
  fanout analysis, and depth computation.
- `z3-solver`: Boolean equivalence, property checking, and counterexample
  generation for combinational cones.

Optional parser helper:

- `lark`: grammar-based parser for the contest Verilog subset if the handwritten
  parser becomes too fragile.

Optional external EDA helpers:

- `yosys`: Verilog normalization, read/write sanity checks, and optional formal
  checks.
- `abc`: cone-level logic optimization experiments such as rewrite and balance.

Reference-only or low-priority helpers:

- `circuitgraph`: useful for prototyping graph/SAT ideas, but should not replace
  the project IR.
- `pyverilog`: useful as a parser/codegen reference, but not a primary dependency
  unless Yosys is unavailable and Lark is insufficient.

### 3.2 Integration Rules

- The backend owns the canonical `Design` IR.
- External tools may be called only through explicit adapter modules, such as
  `parser/yosys_adapter.py` or `eda/abc_adapter.py`.
- External tool output must be parsed back into the project IR before any later
  Tool API operation uses it.
- Every transformation that uses an optional helper must run project-level
  verification before committing the modified design state.
- The system must still support a basic pure-Python path for core contest tasks
  if optional binaries are unavailable in the evaluation environment.

## 4. Runtime Rules

### 4.1 Program Invocation

The executable should support:

```bash
./cada1070_alpha -config <config_file_path>
```

If the official team number changes, only the executable name should change; the
runtime behavior must stay the same.

### 4.2 Input Mode

The program reads natural-language requests from stdin line by line.

Each non-empty line is treated as one request. The next request may arrive only
after stdout contains the matching `#END <id>` tag for the current response.

### 4.3 Output Format

For request id `N`, stdout must be:

```text
#RESPONSE N
<response body>
#END N
```

The same response block must also be appended to the current testcase log file if
a testcase is active.

### 4.4 Testcase State

Each testcase has one evolving design state.

When `begin_testcase` is executed:

- `state.testcase` is set to the new testcase name.
- `state.response_id` is reset to 1.
- `state.design` is cleared.
- `state.previous_results` is cleared.
- `output/logs/<case_name>.log` is created or overwritten.

Every successful transformation modifies the current design. Later requests
operate on the modified design, not the original design.

### 4.5 Transformation Transactions

Transformations must be transactional:

- Validate arguments before modifying the design.
- Work on a copy or rollback-capable representation.
- Run required structural checks after editing.
- Commit the modified design only if hard requirements pass.
- Return a clear rejection if the transformation cannot be safely applied.

## 5. Internal Representation

### 5.1 Gate

```python
@dataclass
class Gate:
    name: str
    type: str
    inputs: list[str]
    output: str
    attrs: dict[str, str] = field(default_factory=dict)
```

Allowed primitive gate types:

```text
and, or, nand, nor, not, buf, xor, xnor
```

All primitive gates have two inputs and one output except `buf` and `not`, which
have one input and one output.

### 5.2 DFF

```python
@dataclass
class DFF:
    name: str
    d: str
    q: str
    clk: str | None = None
    rst: str | None = None
    rst_value: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
```

DFFs are sequential boundaries for combinational path, cone, equivalence, and
depth analysis unless a future tool explicitly asks for sequential reasoning.

### 5.3 Design

```python
@dataclass
class Design:
    module_name: str
    inputs: set[str]
    outputs: set[str]
    wires: set[str]
    gates: dict[str, Gate]
    dffs: dict[str, DFF]
    drivers: dict[str, str]
    fanouts: dict[str, list[str]]
```

Bus signals should be represented internally as bit-level scalar nets, using a
stable naming convention such as `a[0]`, `a[1]`, and so on.

### 5.4 Driver/Fanout Convention

`drivers[net]` maps a net name to one driver id.

Driver id format:

```text
PI:<net>        primary input
CONST:1'b0      constant zero
CONST:1'b1      constant one
GATE:<inst>     gate output driver
DFF:<inst>      dff q output driver
```

`fanouts[net]` maps a net name to a list of sink ids.

Sink id format:

```text
PO:<net>        primary output
GATE:<inst>     gate input sink
DFF:<inst>      dff d/clk/rst input sink
```

## 6. Supported Netlist Scope

The target contest subset is:

- One flattened top module.
- Primitive gate instances with positional pins.
- DFF instances using the contest DFF model.
- Scalar and bus inputs, outputs, and wires.
- Constants `1'b0` and `1'b1`.
- No hierarchy after parsing.

The pure-Python implementation should support the contest subset directly. Yosys
may be used as a normalization helper when available.

## 7. Agent Rules

The LLM planner must return only a JSON object or JSON list using the Tool API in
`docs/tool_spec.md`.

The backend must validate every tool call before execution. Validation includes:

- Known operation name.
- Required arguments.
- Argument types.
- Existing design state when required.
- Existing saved result keys when `targets_from` or similar references are used.

If the LLM returns invalid JSON, an invalid tool call, a checker-rejected plan,
or a high-confidence prompt/plan semantic mismatch, the planner may attempt a
bounded repair pass. The retry count is controlled by `planner.max_retries` and
all pre-dispatch repair reasons share the same budget. If repair fails, the
request is rejected with the unsupported request message or the relevant tool
rejection message.

## 8. Error Handling

For unsupported or ambiguous requests:

```text
I could not map the request to a supported EDA operation. Please rephrase or use a supported task.
```

For invalid tool arguments:

```text
Tool call rejected: <reason>
```

For missing design state:

```text
No design has been loaded yet.
```

For a failed transformation:

```text
Transformation rejected: <reason>
```

## 9. Milestones

### M0: Skeleton Stabilization

- Repository structure exists.
- Core docs exist.
- CLI reads stdin and emits valid response blocks.
- Testcase logging works.
- MVP parser, writer, dispatcher, and smoke test exist.

### M1: Basic Contest Pass

- Scalar and bus gate-level parser works.
- DFFs are represented as sequential boundaries.
- Read/write, path, path avoidance, all-paths-through, max-depth, cone, and gate
  search tools work.
- Basic transformations work: buffer-to-AND, dangling removal, inverter-buffer
  merge, OR-to-NAND/NOT.

### M2: Formal Verification

- Z3-based combinational cone encoding works.
- Equivalence and property checks are supported.
- Transformations run structural and equivalence checks before commit when
  functionality must be preserved.
- Current status: implemented for combinational cones; counterexamples are
  returned when a check fails.

### M3: Optimization

- Fanout buffer insertion supports hard fanout bounds.
- Depth balancing supports buffer insertion with minimal or near-minimal changes.
- Cone optimization can reduce gate count under hard depth or functionality
  constraints.
- Optional Yosys/ABC adapters may be used when available.
- Current status: high-fanout buffer insertion is implemented first and is
  guarded by connectivity, equivalence, and fanout-bound checks.
- Current status: endpoint depth balancing is implemented for independent
  gate-driven destination nets and is guarded by connectivity, equivalence, and
  depth-balance checks.
- Current status: cone optimization has a first local simplification pass for
  redundant buffers and double inverters. Broader Boolean resynthesis is future
  work.

### M4: Submission Hardening

- LLM planner supports schema validation, semantic prompt/plan checking, and
  bounded pre-dispatch repair retry.
- Timeouts and graceful fallback paths exist.
- Regression tests cover PDF-style examples.
- Config files never expose API keys.
