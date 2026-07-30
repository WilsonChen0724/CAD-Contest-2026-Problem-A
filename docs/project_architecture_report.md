# CAD_A Project Architecture Report

Audience: Person A, Person B, Person C  
Baseline: latest local working tree after the all-testcase fixes  
Verification snapshot: `python -m unittest discover -s tests` -> 172 tests OK

## 1. Project Goal

CAD_A is an ICCAD Contest Problem A style prototype for LLM-assisted gate-level
netlist exploration, verification, and transformation.

The central design rule is simple: the LLM never edits Verilog directly. It only
translates natural-language requests into a restricted Tool API plan. The local
backend validates the plan, executes deterministic EDA operations on a canonical
Design IR, and prints contest-style response blocks.

```text
stdin request
  -> main.py request loop
  -> rule / OpenAI / Claude planner
  -> Tool API JSON plan
  -> plan_checker validation
  -> runtime dispatcher
  -> Design IR + parser / analysis / transform / verify backend
  -> #RESPONSE / #END output
```

This architecture gives the team a defensible contribution: LLM flexibility is
kept at the planning layer, while correctness-critical EDA behavior remains
deterministic, inspectable, and testable.

## 2. Repository Architecture

### `main.py`

`main.py` owns the executable request loop.

Important behavior:

- Parses command-line options: `-config`, `-planner`, `--ensure-yosys`.
- Supports planner modes: `rule`, `llm_openai`, `llm_claude`, `llm_both`.
- Reads one non-empty stdin line as one contest request.
- Builds a plan through `_make_plan()`.
- Executes the plan through `runtime.dispatcher.dispatch_plan()`.
- Emits formatted responses through `runtime.response.emit_response()`.
- Keeps stdout clean for contest response blocks; installer/provider debug logs go to stderr.

Important functions:

- `main()`: top-level stdin loop and error handling.
- `_make_plan()`: selects rule planner, OpenAI planner, Claude planner, or OpenAI-then-Claude fallback.
- `_ensure_yosys_available()`: finds system/local Yosys or runs the local installer.

### `agent/`

The `agent` package translates natural language into Tool API plans.

Main files:

- `planner.py`: deterministic rule-based planner for local debugging and release-case coverage.
- `llm_planner.py`: provider-tool planning path with one repair retry.
- `llm_api.py`: provider API wrapper.
- `tool_schema.py`: OpenAI/Anthropic domain tool definitions.
- `plan_checker.py`: local safety boundary.
- `prompt.txt`: LLM instruction and mapping examples.

Important functions:

- `plan_request()`: rule planner entrypoint.
- `_plan_transform()`, `_plan_analysis()`, `_plan_verification()`: rule planner categories.
- `plan_with_llm()`: sends prompt/request to an LLM provider and validates returned tool calls.
- `validate_domain_tool_plan()`: checks one provider domain tool call.
- `validate_plan()`: validates plain Tool API plans.

Current notable fixes:

- Rule planner recognizes more release prompt phrasings, including critical path depth, complete path enumeration between two signals, gate successors, identical-logic equivalence, and XOR-to-4-NAND conversion.
- LLM tool schema groups operations into domain tools: design IO, analysis, transform, and verification.
- The checker rejects unknown operations, missing required arguments, unknown args, invalid types, and empty required strings.

### `runtime/`

The `runtime` package owns state and execution.

Main files:

- `state.py`: mutable testcase state.
- `dispatcher.py`: Tool API execution and response formatting.
- `response.py`: `#RESPONSE N` / `#END N` formatting and testcase log append.
- `config.py`: config loader.

Important objects and functions:

- `CurrentState`: stores current testcase, response id, active design, original design snapshot, previous results, last transform info, and output/log paths.
- `dispatch_plan()`: maps validated operations to backend functions.
- `_run_transactional_transform()`: executes function-preserving transforms on a copied design and commits only after guards pass.
- `_format_*()` helpers: convert structured backend results into contest-style text.

Current notable fixes:

- Large `report_all_paths` results are written to `output/reports/..._paths.txt` when stdout would become too large.
- Stdout shows only the first 20 paths plus the report path.
- Transform and verification responses are formatted to avoid exposing Python internals to contest output.

### `eda/`

The `eda` package owns the canonical IR and deterministic backend algorithms.

Main files:

- `design.py`: `Gate`, `DFF`, and `Design`.
- `graph.py`: rebuilds driver/fanout maps.
- `analysis.py`: structural graph analysis.
- `verify.py`: connectivity, fanout/depth bounds, Boolean equivalence/property checking.
- `transform.py`: netlist transformations and optimization helpers.

Core IR:

```python
Design(
    module_name,
    inputs,
    outputs,
    wires,
    gates: dict[str, Gate],
    dffs: dict[str, DFF],
    drivers,
    fanouts,
)
```

Important graph convention:

- `drivers[net]` records one source such as `PI:n0`, `GATE:g1`, `DFF:ff0`, or `CONST:1'b1`.
- `fanouts[net]` records sinks such as `PO:n8`, `GATE:g2`, or `DFF:ff1`.
- DFFs are treated as sequential boundaries for combinational path, depth, cone, and equivalence analysis.

Important analysis functions:

- `find_path()`: one combinational path, optionally avoiding gates/nets.
- `all_paths()` / `enumerate_paths()`: bounded path enumeration.
- `max_depth()`: longest combinational gate-depth propagation.
- `logic_cone()`: transitive fanin cone.
- `fanout_cone()`: transitive fanout/reachability report.
- `gate_on_max_depth_path()`: checks if a gate lies on a global maximum-depth path.
- `derive_boolean_equation()`: bounded structural Boolean expression expansion.

Important verification functions:

- `check_connectivity()`: missing-driver and duplicate-driver checks.
- `check_fanout()`: max fanout bound check.
- `check_depth()`: src-to-dst depth bound check.
- `check_equivalence()`: Boolean expression vs target signal.
- `check_property()`: Boolean property proof.
- `check_design_equivalence()`: current design vs snapshot or pre-transform design.

Important transform functions:

- `rename_net()`, `rename_gate()`: safe name updates.
- `reconnect_gate_input()`: pin reconnect with guard.
- `constant_propagation()`: simplify constant/redundant gates.
- `insert_buffers_for_fanout()`: satisfy hard fanout bounds.
- `balance_depth_with_buffers()`: equalize structural depth to selected destinations.
- `optimize_cone()`: local cone simplification.
- `replace_xor_with_nand()`, `replace_xnor_with_nor()`, `replace_and_not_with_nand()`, `replace_with_and_not()`: technology mapping style rewrites.
- `merge_equivalent_gates()`: structural duplicate merge.

### `parser/`

The parser layer converts Verilog into the project IR and writes IR back to
Verilog.

Main files:

- `verilog_parser.py`: Yosys-backed parser plus direct fallback parser.
- `verilog_writer.py`: deterministic primitive Verilog writer.
- `yosys_tools.py`: resolves and runs Yosys.

Important functions:

- `parse_verilog()`: primary read path. It rewrites primitive cells as wrappers, uses Yosys JSON, then converts JSON to `Design`.
- `_parse_gate_level_verilog_direct()`: fallback parser for flattened contest-style primitive netlists when Yosys fails on Windows path handling.
- `_normalize_dff_pins()`: supports positional and named-pin DFF styles.
- `write_verilog()`: emits normalized flattened primitive Verilog and validates with Yosys when possible.
- `run_yosys_script()`: runs scripts in a temp working directory to avoid path issues.

Current notable fixes:

- Yosys no longer runs with release testcase directories containing spaces as cwd.
- If OSS CAD Suite reports `GetShortPathName() failed`, the parser falls back to direct contest-subset parsing.
- Writer validation tolerates the same Windows-specific Yosys path failure instead of blocking output.

### `scripts/`

Important scripts:

- `install_yosys.py`, `install_yosys.ps1`, `install_yosys.sh`: local Yosys install helpers.
- `run_release_testcases.py`: runs `release_0706/testcase/testNN/prompt.txt` through `main.py`.

Current runner behavior:

- Runs each testcase in the release directory so prompt paths resolve naturally.
- Separates runner outputs by planner mode.
- Copies generated `testNN_out.v` into the matching runner output folder.
- Uses basic vs non-basic timeout classification.
- Recent fix improves detection of output/write-design prompts so basic IO operations use the correct 60-second timeout.

## 3. Implemented Capability Summary

### Basic IO

- Start testcase and reset local state.
- Read Verilog into Design IR.
- Write transformed Design IR back to Verilog.
- Preserve testcase logs.

### Analysis

- Gate count by type.
- Path existence and avoid-path queries.
- Bounded all-paths reports with artifact output for large results.
- Max combinational depth.
- Fanin cone and cone depth.
- Direct fanout and transitive fanout cone.
- Gate connection reports.
- Primary input/output reports.
- DFF clock-domain and register path reports.
- Boolean equation derivation when tractable.

### Verification

- Connectivity checks.
- Fanout and depth bound checks.
- Combinational equivalence and property checking.
- Original-loaded-netlist equivalence.
- Last-transform-input equivalence.
- Z3 is preferred; small brute-force fallback exists.

### Transformation / Optimization

- Rename net/gate.
- Reconnect gate input with equivalence guard.
- Remove dangling logic.
- Constant propagation.
- Collapse back-to-back inverters and inverter-buffer patterns.
- Technology mapping rewrites for NAND/NOR/AND/NOT/XOR/XNOR style tasks.
- High-fanout buffer insertion.
- Dedicated buffer insertion.
- Structural depth balancing.
- Local cone/design-depth optimization.
- Structural duplicate merge.

## 4. Main Contributions

### Contribution 1: Guarded LLM-to-EDA Architecture

The project is not just an LLM wrapper. It separates intent translation from
EDA execution:

- LLM proposes only Tool API calls.
- Checker enforces local schema and safety rules.
- Dispatcher executes deterministic backend operations.
- Invalid or unsupported requests are rejected before any design mutation.

### Contribution 2: Canonical IR With Open-Source Helpers

Yosys is used as a parser/writer normalization helper, but the project keeps its
own canonical Design IR. This makes the backend explainable and lets the team
apply custom graph algorithms and transactional guards.

### Contribution 3: Formal Guarding of Transformations

Function-preserving transformations are treated as transactions:

1. Copy the current design.
2. Apply the transform to the copy.
3. Rebuild graph maps.
4. Run connectivity/equivalence/constraint checks.
5. Commit only if the guards pass.

This is the main correctness story for transformation and optimization tasks.

### Contribution 4: Practical Contest Robustness

The latest fixes target real release-case failure modes:

- Windows/Yosys path failures.
- Prompt wording variation.
- Large all-paths output.
- Timeout classification for write-design prompts.
- DFF and flattened gate-level parsing edge cases.

## 5. Design Choices And Rationale

### Why not let the LLM edit Verilog?

Direct Verilog editing is difficult to verify and easy to hallucinate. The Tool
API boundary makes every action explicit, typed, validated, and testable.

### Why keep our own Design IR?

The IR lets us implement graph algorithms, transactional transforms, and custom
response formatting without depending on opaque external tool state.

### Why use Yosys?

Yosys is a strong normalization helper for Verilog syntax and sanity checks. It
reduces parser risk while still allowing the project to own the semantic IR.

### Why use Z3?

Z3 provides a precise way to prove combinational equivalence/properties and
return counterexamples. This is useful both for direct verification prompts and
as a transform guard.

### Why start with local rewrites before ABC/Yosys optimization?

Local rewrites are deterministic, explainable, and easy to guard. ABC/Yosys
resynthesis can be stronger, but it raises name preservation and debugging
risks. The current architecture can add it later as an optional candidate
backend behind the same verification guards.

## 6. Current Verification Evidence

Local unit tests:

```text
Ran 172 tests in 0.387s
OK
```

Covered areas include:

- parser/writer behavior,
- rule planner mappings,
- LLM planner validation and retry behavior,
- plan checker schema checks,
- dispatcher execution and formatting,
- analysis algorithms,
- transformation guards,
- formal verification helpers,
- release testcase runner helpers.

## 7. Team Ownership Map

### Person A

Primary ownership:

- parser/writer,
- Design IR shape,
- graph rebuild correctness,
- structural analysis algorithms.

### Person B

Primary ownership:

- LLM planner,
- provider tool schema,
- prompt mapping,
- plan checker compatibility,
- LLM mode release testing.

### Person C

Primary ownership:

- runtime dispatcher,
- transformation execution,
- formal verification integration,
- optimization guards,
- release testcase runner behavior.

## 8. Recommended Presentation Thesis

The strongest six-minute thesis is:

> We built a guarded LLM-assisted EDA system where the LLM handles natural
> language planning, while deterministic backend code owns parsing, graph
> analysis, transformation, and formal verification. The main contribution is
> the safety architecture: Tool API validation, canonical IR, transactional
> transforms, and formal guards.
