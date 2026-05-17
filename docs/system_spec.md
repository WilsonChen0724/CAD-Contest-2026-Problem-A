# System Specification

## 1. Project Goal

Build a system that accepts natural-language requests, translates them into deterministic EDA tool operations, executes those operations on the current gate-level Verilog design state, and returns analysis results or writes transformed netlists.

The LLM must not directly edit Verilog. The LLM should produce a structured plan using the allowed Tool API. The deterministic backend executes the plan.

## 2. High-Level Architecture

```text
stdin request
    ↓
runtime/main loop
    ↓
agent/planner.py
    ↓
JSON-like tool plan
    ↓
runtime/dispatcher.py
    ↓
eda backend
    ↓
response formatter
    ↓
stdout + testcase log
```

## 3. Runtime Rules

### 3.1 Program Invocation

The executable should support:

```bash
./cada0001_alpha -config <config_file_path>
```

### 3.2 Input Mode

The program reads natural-language requests from stdin line by line.

Each non-empty line is treated as one request.

### 3.3 Output Format

For request id `N`, stdout must be:

```text
#RESPONSE N
<response body>
#END N
```

The same response block must also be appended to the current testcase log file if a testcase is active.

### 3.4 Testcase State

Each testcase has one evolving design state.

Every transformation modifies the current design. Later requests operate on the modified design, not the original design.

## 4. Internal Representation

### 4.1 Gate

```python
@dataclass
class Gate:
    name: str
    type: str
    inputs: list[str]
    output: str
    attrs: dict[str, str] = field(default_factory=dict)
```

Allowed primitive gate types in the first milestone:

```text
and, or, nand, nor, not, buf, xor, xnor
```

### 4.2 DFF

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

### 4.3 Design

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

### 4.4 Driver/Fanout Convention

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

## 5. Netlist Scope for MVP

The first milestone assumes:

- One top module.
- Flattened gate-level Verilog.
- Positional primitive instances, such as `and U1(y, a, b);`.
- Scalar signals first.
- Bus support can be added after scalar parser works.
- Constants `1'b0` and `1'b1` are accepted as nets with constant drivers.

## 6. Agent Rule

The LLM agent must return only a JSON object or JSON list using the Tool API in `docs/tool_spec.md`.

The backend must validate every tool call before execution.

## 7. Error Handling

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

## 8. Day1 Completion Definition

Day1 is complete when:

- The repository structure exists.
- `docs/system_spec.md` exists.
- `docs/tool_spec.md` exists.
- `eda/design.py` defines the IR.
- `runtime/state.py` defines current testcase state.
- `main.py` can read stdin and print valid response blocks.
- `cada0001_alpha` runs the program.
