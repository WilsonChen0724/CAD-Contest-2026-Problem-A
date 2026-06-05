# CADA1070 v0.3.1

This repository is an ICCAD Contest Problem A style prototype for
LLM-assisted netlist exploration and transformation.

The runtime accepts natural-language requests from stdin, turns each request
into a restricted Tool API plan, validates the plan, executes deterministic EDA
backend operations on the current gate-level Verilog design state, and prints
contest-style response blocks.

The LLM never edits Verilog directly. In LLM mode it must call one provider
domain tool from `agent/tool_schema.py` (`run_design_io_plan`,
`run_analysis_plan`, `run_transform_plan`, or `run_verify_plan`). The returned
tool-call arguments are normalized into the project Tool API plan, checked by
`agent/plan_checker.py`, and then executed through `runtime/dispatcher.py`.
The broader target API is documented in `docs/tool_spec.md`; this README marks
which parts are implemented in v0.3.1.

## Architecture

```text
stdin request
    |
    v
main.py request loop
    |
    v
rule planner / OpenAI planner / Claude planner / fallback planner
    |
    v
validated Tool API JSON plan
    |
    v
runtime/dispatcher.py
    |
    v
Yosys-backed Verilog parser/writer + Design IR + EDA backend
    |
    v
stdout response + testcase log + optional output netlist
```

## What v0.3.1 Supports

### Planner modes

`main.py` supports three planner modes:

```bash
python main.py -config config.example.yaml -planner rule
python main.py -config config.example.yaml -planner llm_openai
python main.py -config config.example.yaml -planner llm_claude
python main.py -config config.example.yaml -planner llm_both
```

- `rule`: deterministic keyword/rule planner. This is the default and needs no
  API key.
- `llm_openai`: sends each request to the OpenAI Responses API with four
  domain function tools and validates the returned tool-call plan.
- `llm_claude`: sends each request to the Anthropic Messages API with the same
  four domain tools and validates the returned `tool_use` plan.
- `llm_both`: tries OpenAI first, then falls back to Claude when OpenAI is
  missing, unavailable, or returns a checker-rejected plan.

The LLM path includes one repair retry when the model returns an invalid tool
call or a checker-rejected plan. Raw provider tool calls are logged to stderr as
pretty-printed `[llm-tool-call]` JSON blocks, so contest stdout remains clean.

### Implemented Tool API operations

These operations are wired through both the plan checker and dispatcher:

- `begin_testcase`
- `read_design`
- `write_design`
- `find_path`
- `all_paths_pass_through`
- `max_depth`
- `logic_cone`
- `report_outputs_by_cone_size`
- `find_gates`
- `same_clock_domain`
- `replace_buffers_with_and`
- `remove_dangling`
- `replace_inv_buf_with_inv`
- `replace_or_with_nand_not`
- `insert_buffers_for_fanout`
- `balance_depth_with_buffers`
- `optimize_cone`
- `check_connectivity`
- `check_fanout`
- `check_depth`
- `check_equivalence`
- `check_property`
- `unsupported`

Multi-step plans with a top-level `steps` array are supported. A step may use
`save_as` to store a result in testcase-local state, and later steps can reuse
saved gate lists with arguments such as `targets_from`.

### Verilog frontend and backend

The parser/writer path uses a Yosys-backed flow:

- `parser/verilog_parser.py` reads one flattened top module through Yosys JSON.
- Primitive instances are temporarily rewritten as private wrapper cells so
  instance names and buffer cells are preserved.
- Supported primitive gates: `and`, `or`, `nand`, `nor`, `not`, `buf`, `xor`,
  `xnor`.
- Supported sequential cells: positional `dff` style cells with
  `(q, d, clk)` or `(q, d, clk, rst)`.
- Bus ports and wires are expanded into bit-select nets in the internal
  `Design` IR, then re-emitted as bus declarations when possible.
- `parser/verilog_writer.py` emits deterministic primitive Verilog and checks
  the generated result with Yosys before writing it.

### Analysis and verification

The current backend includes:

- path search that does not cross DFF boundaries,
- longest combinational max-depth propagation with loop guarding,
- fanin logic-cone collection,
- gate search by type and/or name substring,
- fanout-bound checking,
- depth-bound checking,
- connectivity checking for missing and duplicate drivers.
- combinational equivalence/property checking, using `z3-solver` when
  installed and a small brute-force fallback otherwise.
- transactional high-fanout buffer insertion guarded by connectivity,
  equivalence, and final fanout-bound checks.
- transactional endpoint depth balancing with inserted buffers, guarded by
  connectivity, equivalence, and final depth-balance checks.
- transactional cone optimization with local buffer/double-inverter
  simplification, guarded by connectivity, equivalence, and optional depth
  constraints.

Additional analysis helpers exist in `eda/analysis.py` for fanout cones and
related structural reports. Primary-output cone-size reports,
all-paths-through checks, and same-clock-domain DFF checks are exposed through
the dispatcher Tool API.

### Transformation

The implemented transformations are:

- `replace_buffers_with_and`: selected one-input `buf` gates are changed to
  two-input `and` gates using the requested extra input net.
- `remove_dangling`: gates, DFFs, and internal nets that do not contribute to
  any primary output are removed.
- `replace_inv_buf_with_inv`: safe inverter-buffer chains are collapsed into a
  single inverter when the intermediate net has no other fanout.
- `replace_or_with_nand_not`: 2-input OR gates in a requested cone are rewritten
  as equivalent NAND/NOT logic.

Function-preserving transformations are transactional: they run on a copied
design, pass connectivity checks, and must preserve common primary-output
functions before the modified design is committed.

## Requirements

- Python 3.10 or newer.
- Yosys, either installed on `PATH` or installed locally under
  `third_party/yosys/oss-cad-suite`.
- `z3-solver` is recommended for formal equivalence/property checks. Without
  it, small formal checks fall back to brute-force enumeration.
- Optional `OPENAI_API_KEY` for `-planner llm_openai`; optional `ANTHROPIC_API_KEY` for `-planner llm_claude`; both are useful for `-planner llm_both`.

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Install or verify a local Yosys copy:

```bash
python scripts/install_yosys.py
```

Reinstall from scratch:

```bash
python scripts/install_yosys.py --force
```

You can also ask the main program to ensure Yosys exists before reading stdin:

```bash
python main.py --ensure-yosys -config config.example.yaml
```

On Linux/macOS, the shell launcher is:

```bash
chmod +x cada1070_alpha
./cada1070_alpha --ensure-yosys -config config.example.yaml < tests/smoke_input.txt
```

On Windows PowerShell, run Python directly:

```powershell
Get-Content .\tests\smoke_input.txt | python .\main.py --ensure-yosys -config .\config.example.yaml
```

Installer logs go to stderr so stdout can remain in the contest response
format.

## Configuration

`config.example.yaml` shows the supported shape:

```yaml
provider: "openai"
openai:
  api_key: "<YOUR_API_KEY>"
  model: "gpt-4.1-mini"
anthropic:
  api_key: "<YOUR_API_KEY>"
  model: "claude-haiku-4-5-20251001"
generation:
  temperature: 0.2
  max_output_tokens: 4096
```

For local development, prefer setting `OPENAI_API_KEY` in the environment or
using an untracked `config.yaml`. Do not commit real API keys.

## Smoke Test

Run the default deterministic planner flow:

```bash
python main.py --ensure-yosys -config config.example.yaml < tests/smoke_input.txt
```

Expected behavior:

- The program reads each non-empty stdin line as one request.
- It prints responses wrapped by `#RESPONSE <id>` and `#END <id>`.
- `begin_testcase` creates `output/logs/<case_name>.log`.
- Later requests operate on the current testcase design state.
- The smoke input loads `tests/design/netlist/test8.v`, finds `_gc__`
  buffers, replaces them with AND gates, reports max depth, and writes
  `output/test8_out.v`.

## Release Testcase Runner

The bundled unit and smoke tests still use the small `tests/` fixtures by
default. To run the larger `A_release testcase_0510` prompt folders through the
same stdin/stdout contest loop, use:

```bash
python scripts/run_release_testcases.py --ensure-yosys
```

Run one case:

```bash
python scripts/run_release_testcases.py --case test01 --ensure-yosys
```

Use an LLM planner, or compare both providers:

```bash
python scripts/run_release_testcases.py --planner llm_openai --ensure-yosys
python scripts/run_release_testcases.py --planner llm_claude --ensure-yosys
python scripts/run_release_testcases.py --planner llm_both --ensure-yosys
```

The runner executes each `testcase/testNN/prompt.txt` with `main.py` using the
release directory as the working directory, so prompt paths such as
`testcase/test01/test01.v` resolve naturally. Per-case stdout/stderr logs are
written under `A_release testcase_0510/runner_output/<planner>/`, so `llm_openai`, `llm_claude`, and `rule` runs do not overwrite each other. In the release runner, `--planner llm_both` runs `llm_openai` and `llm_claude` separately and records both provider outputs. Generated `testNN_out.v` files are also copied into the matching planner output folder when present.

During development, unsupported responses are counted but do not fail the run.
After the remaining backend tools are implemented, enable stricter regression
behavior with:

```bash
python scripts/run_release_testcases.py --ensure-yosys --fail-on-unsupported --fail-on-error
```

For a fuller setup and testing walkthrough for teammates, see `docs/testing_guide.md`.

## Run Tests

```bash
python -m unittest discover -s tests
```

Parser and writer tests require Yosys. If Yosys is not on `PATH`, install it
with `python scripts/install_yosys.py` or run commands through
`python main.py --ensure-yosys ...` first.

## Repository Layout

```text
agent/       rule planner, LLM API wrapper, JSON plan checker, prompt
eda/         Design IR, graph maps, analysis, transforms, verification
parser/      Yosys-backed Verilog parser/writer and Yosys resolver
runtime/     state, dispatcher, response formatter, config loader
scripts/     cross-platform Yosys install helpers
docs/        system spec, Tool API spec, workflow notes
tests/       unit tests, smoke input, sample netlists
third_party/ local Yosys install location, not committed
output/      generated logs and output netlists
```

## Open-Source Helper Policy

Open-source tools may be used behind deterministic adapters. The canonical
design state remains the project `Design` IR.

- Yosys is the current parser/writer syntax and normalization helper.
- `networkx` remains a recommended future helper for graph tasks.
- `z3-solver` is used for formal tasks when installed; otherwise small checks
  fall back to brute-force enumeration.
- Optional future optimization adapters may use Yosys or ABC, but any
  function-preserving transformation should verify before commit.

The pure Python backend still owns the IR, graph traversal, response behavior,
and Tool API safety boundary.

## Known Limits in v0.3.1

- The dispatcher exposes the implemented operation list above, while
  `docs/tool_spec.md` still describes a broader contest target.
- Formal checks are combinational-only in this version. Sequential
  unrolling/property checking is not implemented yet.
- Cone optimization is implemented as a first local simplification pass for
  redundant buffers and double inverters. General Boolean resynthesis is not
  implemented yet.
- Fanout-buffer insertion is implemented for gate/DFF sinks; primary-output
  sinks remain directly tied to the original net and count against the root
  fanout budget.
- Depth balancing is implemented for independent gate-driven destination nets
  by inserting buffer chains before the selected endpoints.
- Depth balancing is a structural logic-depth helper, not a physical timing
  optimizer. It is intended for requests that require path-depth alignment or
  for preparing normalized cones before later optimization. It counts primitive
  gates as depth stages and does not model Liberty delay, placement, routing,
  slew, or clock skew.
- Current depth balancing pads each endpoint independently. Future versions
  should share buffers across common subtrees, support DFF-input/primary-output
  endpoints when safe, and accept hard constraints such as max inserted buffers
  or max final depth.
- The writer emits a normalized flattened primitive style instead of preserving
  original formatting or comments.
- Named-pin, library-specific sequential cells are future work beyond the
  normalized DFF support.

## Milestones

### M0: Skeleton Stabilization

- Repository structure exists.
- CLI reads stdin and emits valid response blocks.
- Testcase logging works.
- MVP parser, writer, dispatcher, and smoke test exist.

### M1: Basic Contest Pass

- Scalar and bus netlists parse into IR.
- DFFs are represented as sequential boundaries.
- Read/write, path, max-depth, logic-cone, gate-search, and basic verification
  tools work.
- Basic buffer-to-AND transformation works.

### M2: Formal Verification

- Z3-based combinational cone encoding works.
- Equivalence and property checks are supported.
- Function-preserving transformations verify before commit.

### M3: Optimization

- Fanout buffer insertion satisfies hard fanout bounds.
- Depth balancing supports buffer insertion.
- Cone optimization reduces simple redundant logic under hard constraints.
- Optional Yosys/ABC adapters may be used behind verification guards.

### M4: Submission Hardening

- LLM planner supports schema validation and repair.
- Runtime handles invalid requests and failed transformations gracefully.
- Regression tests cover PDF-style examples.
- Config files do not expose API keys.

## Team Division

- Person A, data-structure background: IR, parser, writer, graph, analysis.
- Person B, ML background: LLM planner, JSON schema, prompt, tool spec.
- Person C, IC contest background: runtime, dispatcher, transformations,
  verification, optional optimization adapters.

See `docs/work_division.md` for the detailed milestone-based ownership plan.

## Version Notes

### v0.3.1 - Formal checks and guarded optimization tools (2026-05-28)

This version extends the v0.3.0 Yosys-backed baseline with a wider dispatcher
surface, formal verification helpers, and first-pass optimization transforms.

1. More Tool API operations are production-wired

   The checker, rule planner, prompt, and dispatcher now cover additional
   analysis, transformation, optimization, and verification tools:

   - `all_paths_pass_through`
   - `report_outputs_by_cone_size`
   - `same_clock_domain`
   - `remove_dangling`
   - `replace_inv_buf_with_inv`
   - `replace_or_with_nand_not`
   - `insert_buffers_for_fanout`
   - `balance_depth_with_buffers`
   - `optimize_cone`
   - `check_equivalence`
   - `check_property`

2. Combinational formal verification

   `eda/verify.py` now supports expression equivalence checks and Boolean
   property checks over combinational cones. The preferred backend is
   `z3-solver`; small checks can fall back to brute-force enumeration.

3. Transactional transformation safety

   Function-preserving dispatcher transforms now run on copied design state and
   commit only after connectivity and equivalence checks pass. This protects the
   active testcase design from failed rewrites.

4. New structural rewrites

   v0.3.1 turns earlier transformation stubs into implemented operations:
   dangling-logic removal, inverter-buffer chain collapse, and OR-to-NAND/NOT
   cone rewriting.

5. First optimization support

   High-fanout buffer insertion, endpoint depth balancing, and local cone
   optimization are now available. These are conservative structural helpers:
   they preserve function, check hard constraints after rewriting, and avoid
   physical timing claims.

6. Regression coverage

   Tests were added for the expanded rule planner mappings, plan checker
   schemas, dispatcher transactions, formal verification checks, fanout buffer
   insertion, depth balancing, and cone optimization.

### v0.3.0 - Yosys-backed frontend and planner hardening (2026-05-25)

This version documents the current mainline behavior after integrating the
Yosys parser/writer path and the safer planner/runtime boundary.

1. Yosys parser and writer flow

   `parse_verilog()` now uses Yosys JSON as the frontend and converts the
   result into the project `Design` IR. Primitive gates are wrapped before
   Yosys parsing so gate instance names and `buf` cells survive normalization.

   `write_verilog()` emits deterministic primitive Verilog, reconstructs bus
   declarations where possible, emits normalized positional DFF instances, and
   validates the generated module with Yosys before writing the file.

2. Local Yosys installation support

   The repository now includes cross-platform install helpers under `scripts/`
   and a local toolchain home under `third_party/yosys/`.

   ```bash
   python scripts/install_yosys.py
   python main.py --ensure-yosys -config config.example.yaml
   ```

   Windows and Linux local OSS CAD Suite layouts are resolved by
   `parser/yosys_tools.py`.

3. Planner modes

   `main.py` now supports `rule`, `llm_openai`, `llm_claude`, and `llm_both` planner modes. The LLM
   planner calls provider tool APIs, requests domain tool calls, validates
   the result through `plan_checker.py`, and retries once with a repair request
   when validation fails.

4. Stronger Tool API safety boundary

   The checker and dispatcher now share the implemented operation set and reject
   unsupported operations, unknown fields, missing required arguments, and basic
   type mismatches before backend execution.

5. Expanded analysis coverage

   The analysis module includes tested helpers for all-paths-through queries,
   fanout cone reports, primary-output cone sizes, and DFF relationship reports.
   The dispatcher-exposed surface remains intentionally smaller in v0.3.0.

6. Regression coverage

   Unit tests cover Yosys resolution, parser/writer behavior, LLM repair retry,
   plan validation, dispatcher behavior, analysis helpers, transformations, and
   verification checks.

### v0.2.0 - Day2 correctness fixes (2026-05-20)

This version records five small but important correctness fixes found after the
Day2 LLM planner integration.

1. `cada1070_alpha` line-ending and executable-mode handling

   Added `.gitattributes` rules so shell scripts and source files keep stable LF
   line endings across Windows/Linux environments. The `cada1070_alpha` launcher
   is also intended to be stored with executable permission in Git, so
   Linux/macOS users can run it with:

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

   The Verilog parser supports simple bus declarations and expands them into
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

   The writer can emit these bus declarations and DFF instances back to
   Verilog. Full library-specific named-pin cell parsing is still future work.

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
