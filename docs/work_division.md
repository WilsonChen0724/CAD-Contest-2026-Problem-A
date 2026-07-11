# Work Division and Milestones

This file replaces the original Day1-only division with a milestone-based plan.
The team should still keep ownership boundaries clear so Person A, B, and C can
work in parallel.

Related planning documents:

- `docs/current_progress_and_next_features.md` summarizes current capability,
  testcase coverage gaps, and the next implementation priorities.
- `docs/implementation_alternatives.md` records alternative implementation
  approaches for optimization, renaming, reconnect, and equivalence scope.

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
- Core structural transformations are implemented and wired through the
  dispatcher. Function-preserving transformations are now guarded by
  connectivity and primary-output equivalence checks before commit.
- Combinational equivalence/property checking is now implemented for the first
  Tool API version. It uses `z3-solver` when installed and a small brute-force
  fallback otherwise.
- Function-preserving transformations are now automatically checked with
  `check_design_equivalence` before commit.
- M3 has started: `insert_buffers_for_fanout` is implemented as a transactional
  buffer-tree transform guarded by connectivity, equivalence, and final fanout
  checks. `balance_depth_with_buffers` is implemented for independent
  gate-driven endpoints and is guarded by connectivity, equivalence, and final
  depth-balance checks. `optimize_cone` is implemented as a first local
  simplification pass for redundant buffers and double inverters.

## Beta P0/P1 Ownership

Official response-time policy for local beta testing:

- Basic operations from Section 4.1, such as begin/read/write, use 60 seconds.
- All other analysis, transformation, optimization, and verification requests
  use 300 seconds.
- The release runner should therefore use
  `--basic-timeout 60 --timeout 300` for official-style regression runs.

### P0: Submission-Critical Work

| Person | Owner Area | P0 Responsibility | Done When |
| --- | --- | --- | --- |
| Person A | Parser, writer, graph IR | Fix any load/write/connectivity rebuild issue, especially bus names, DFF pins, constants, and round-trip Verilog normalization. | All 40 cases can load/write without parser-caused runtime errors. |
| Person B | Planner, tool schema, prompt mapping | Keep `agent/tool_schema.py`, `agent/plan_checker.py`, planner prompts, and semantic guards synchronized with implemented backend ops. | LLM mode has no unsupported responses caused by missing schema or wrong operation mapping. |
| Person C | Runner, validator, transform guards | Run ledger-based regression, export validator JSONL/CSV metrics, classify FAIL/INCONCLUSIVE, and keep guarded transforms transactional. | Runner has no missing/error/unsupported responses, and validator findings are either fixed or documented with evidence. |

### P1: Beta Hardening

| Person | Owner Area | P1 Responsibility |
| --- | --- | --- |
| Person A | Analysis coverage | Improve register-path filtering, floating/unconnected-net reports, and cut/articulation reports when test prompts require them. |
| Person B | LLM robustness | Add more paraphrase tests, provider retry checks, and semantic guard cases for unusual prompt wording. |
| Person C | External validation | Reduce `INCONCLUSIVE` cases using selected-output ABC/Yosys equivalence, report-file validation for large outputs, and aggregate QoR metric summaries. |

### Person C Immediate Checklist

Person C owns the beta validation loop:

```powershell
python -m unittest discover -s tests
python scripts\run_release_testcases.py --all --planner rule --validation-ledger --fail-on-error --fail-on-unsupported --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner rule --all --output outputs\validator_rule.jsonl --metrics-output outputs\validator_rule_metrics.csv
python scripts\run_release_testcases.py --all --planner llm_openai --validation-ledger --basic-timeout 60 --timeout 300
python scripts\validate_release_outputs.py --planner llm_openai --all --output outputs\validator_llm_openai.jsonl --metrics-output outputs\validator_llm_openai_metrics.csv
```

Person C should treat a validator `FAIL` as a concrete bug unless the oracle is
proven wrong. An `INCONCLUSIVE` is not a pass; it must be documented with the
reason and the next stronger oracle, such as selected-output equivalence,
bounded file-output validation, or a timeout-aware ABC/Yosys check.

Depth-balancing rationale:

- Equalizing combinational depth from one source to multiple destinations is a
  useful primitive when a contest request asks for delay/path-depth alignment,
  when downstream logic expects matched logic stages, or when a later optimizer
  needs paths normalized before applying local rewrites.
- The current implementation treats each primitive gate, including `buf`, as
  one logic-depth stage. It preserves Boolean functionality but does not model
  physical timing, cell delay, placement, routing, or clock skew.

Depth-balancing limitations and improvement targets:

- Current limit: only independent gate-driven destination nets are supported.
  Primary inputs, DFF outputs, constants, and already-connected destination
  pairs are rejected.
- Current limit: the algorithm pads endpoints independently, so it cannot share
  inserted buffers across common subtrees or optimize globally.
- Current limit: it balances maximum combinational gate depth only; it does not
  balance min/max timing windows, load, slew, or physical delay.
- Improvement target: support shared-prefix buffer insertion for destinations
  in the same fanout tree.
- Improvement target: support balancing to DFF inputs and primary outputs by
  redirecting sinks when safe.
- Improvement target: add optional hard limits, such as max inserted buffers,
  max final depth, or max fanout after depth balancing.
- Improvement target: integrate ABC/Yosys or Liberty-aware timing estimates
  behind the existing equivalence and structural verification guards.

Cone-optimization limitations and improvement targets:

- Current limit: `optimize_cone` only applies local function-preserving rules:
  removing internal buffers and simplifying double inverters.
- Current limit: it does not perform Boolean resynthesis, algebraic factoring,
  technology mapping, or area/delay tradeoff search.
- Current limit: `max_depth` is checked as a hard post-condition, but the
  optimizer does not actively search alternative rewrites to satisfy a failing
  depth bound.
- Improvement target: add constant propagation and idempotent simplifications
  such as `and(a, a) -> a` and `or(a, a) -> a`.
- Improvement target: add an optional Yosys/ABC backend path for larger cones,
  followed by the existing equivalence and structural guards.

Equivalence scope:

- Current equivalence checks are combinational-only.
- DFFs are treated as sequential boundaries.
- Multi-cycle sequential equivalence is intentionally out of scope for the next
  phase unless testcase requirements change.

## Merge-To-Main Handoff Status

This section records the ownership split before merging the current
`C_M3_optimization` work back to `main`.

Current merge-ready Person C changes:

- Structural report tools are wired through the Tool API:
  `report_gate_counts`, `report_fanout`, and `report_gate_connections`.
- `report_fanout` now accepts both net names and gate/DFF instance names, so
  prompts such as "number of gates driven by g0" can resolve `g0` as a gate
  instance and report the fanout of its output net.
- `rename_net` updates declarations and all structural references through a
  shared net-reference helper and commits only after connectivity and
  combinational equivalence checks pass.
- `constant_propagation` simplifies constant and redundant gate inputs under
  connectivity and combinational equivalence guards.
- `check_equivalent_to_original` compares the current design against the
  snapshot captured by `read_design`.
- The release testcase runner now reports progress, response-count mismatch,
  unsupported responses, error markers, and per-case timeouts.

Do not merge generated testcase artifacts:

- `A_release testcase_0510/runner_output/`
- `A_release testcase_0510/test*_out.v`

Current known blocker outside Person C:

- Release testcases from `test31` onward contain named-pin DFF instances such
  as `dff g906(.RN(n1), .SN(1'b1), .CK(n0), .D(n3238), .Q(n14[0]));`.
  The parser currently handles positional DFFs, so these cases fail at
  `read_design` before analysis, transformation, or verification tools can run.
  This is a Person A parser task.

Post-merge ownership:

- Person A should first add named-pin DFF parser support, then expose missing
  graph-analysis tools such as full path enumeration, fanout cone reports,
  cone-local gate type counts, and output cone ranking.
- Person B should stabilize planner/schema coverage for the new and upcoming
  tools, especially prompt variants for gate-instance fanout, immediate
  successors, rename requests, fanout cones, cone-local counts, and
  original-netlist equivalence. New backend operations should be recorded in
  docs first; `agent/tool_schema.py` should be updated only after team review.
- Person C should continue transformation and verification work after the
  merge, starting with `constant_propagation`, `rename_gate`, and guarded
  `reconnect_gate_input`.

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

Next concrete tasks:

- Add parser support for named-pin DFF cells with `Q`, `D`, `CK`/`CLK`, and
  optional reset/set pins such as `RN`, `SN`, `RST`, or `RESET`.
- Add parser regression tests for named-pin DFFs before running `test31` and
  later release cases.
- Expose `fanout_cone` as a dispatcher/tool-schema operation after parser
  loading is stable.
- Implement bounded `all_paths` enumeration so "complete enumeration of paths"
  prompts do not keep returning only one example path.
- Add cone-local reports: gate type counts inside a cone, shared gates between
  two fanin cones, and largest/deepest output cone ranking.

Milestone targets:

- M1: parser, writer, graph, and core analysis tools.
- M2: cone extraction support for formal verification.
- M3: graph support for fanout and depth optimization.

## Person B: LLM Agent / Tool Schema / Prompt

Owner files:

```text
agent/planner.py
agent/llm_api.py
agent/llm_planner.py
agent/intent_classifier.py
agent/prompt.txt
docs/tool_spec.md
```

Primary responsibilities:

- Maintain `docs/tool_spec.md` as the exact contract exposed to the LLM.
- Replace or supplement the rule-based planner with an LLM JSON planner.
- Ensure the LLM only emits allowed Tool API operations.
- Add JSON/schema validation before dispatch.
- Maintain bounded pre-dispatch repair retry for invalid LLM output and
  semantic prompt/plan mismatches.
- Write prompt examples for PDF-style requests.
- Add planner tests for paraphrases and multi-step requests.

Next concrete tasks:

- Keep provider tool schemas synchronized with approved backend operations.
  Use `docs/tool_spec.md`, the provider schemas, plan checker, and regression
  tests as the current source of truth.
- Update planner tests for gate-instance fanout phrases, such as "number of
  gates driven by g0" and "immediate successors of gate g0".
- Maintain regression coverage for `fanout_cone`, bounded `all_paths`,
  cone-local gate counts, output cone ranking, and provider tool-call wording.
- Keep `agent/tool_schema.py`, `agent/plan_checker.py`, `agent/planner.py`,
  `agent/prompt.txt`, and `docs/tool_spec.md` synchronized whenever a backend
  operation is approved for LLM exposure.
- Add LLM regression coverage for rename variants and original-equivalence
  variants such as "last loaded from disk" and "still equivalent to original".

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
- Next priority: implement `rename_net`, original-snapshot equivalence, and
  constant propagation before riskier pin reconnect or full resynthesis tasks.

Next concrete tasks:

- Commit the gate-instance fanout fix before merging to main.
- Keep generated release outputs out of Git.
- Implement `rename_gate` for instance identifier changes.
- Implement guarded `reconnect_gate_input` after shared reference-update
  utilities are stable.

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

- Combinational cones can be encoded into Z3. **Done for combinational gates.**
- `check_equivalence` and `check_property` return counterexamples when false.
  **Done for the first Tool API version.**
- Function-preserving transformations are checked before commit. **Done.**

### M3: Optimization

Done when:

- High-fanout buffer insertion satisfies max-fanout bounds. **First version
  implemented for gate/DFF sinks.**
- Depth balancing inserts a minimal or near-minimal number of buffers. **First
  version implemented for independent gate-driven destination nets.**
- Cone optimization satisfies hard constraints before minimizing gate count.
  **First local simplification version implemented.**
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
