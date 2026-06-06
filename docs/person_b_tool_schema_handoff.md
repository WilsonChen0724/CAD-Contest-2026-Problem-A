# Person B Tool Schema Handoff

This note records the backend and rule-planner changes that are ready for
Person B review before exposing them through `agent/tool_schema.py` for direct
LLM-mode tests.

## Current Status

The following operations are implemented in the local Tool API path:

- `agent/planner.py`: deterministic rule mappings exist.
- `agent/plan_checker.py`: local validation accepts the operations.
- `runtime/dispatcher.py`: dispatcher handlers exist.
- `docs/tool_spec.md`: Tool API contract is documented.
- Unit tests and release test32 rule/hybrid runs pass.

Important: `agent/tool_schema.py` has intentionally not been modified in this
branch. Direct `--planner llm` mode will not know these new operations until
Person B updates the OpenAI function schema.

## Operations To Add To OpenAI Tool Schema

Recommended grouping for `TOOL_ALLOWED_OPS`:

- `run_analysis_plan`
  - `report_io_counts`
  - `gate_on_max_depth_path`
  - `report_register_paths`
  - `report_fanout_cone`
  - `report_constant_input_gates`

- `run_transform_plan`
  - `replace_nand_const1_with_not`
  - `constant_propagation`
  - `rename_net`

- `run_verify_plan`
  - `check_equivalent_to_original`

`report_gate_counts`, `report_fanout`, and `report_gate_connections` should
also be checked because they were already backend-wired and are used heavily by
release prompts.

## New Operation Shapes

```json
{"op": "report_io_counts", "args": {}}
```

```json
{"op": "gate_on_max_depth_path", "args": {"gate": "g0"}}
```

```json
{"op": "report_register_paths", "args": {"max_paths": 200}}
```

`max_paths` is optional in the local plan checker.

```json
{"op": "report_fanout_cone", "args": {"source": "n0"}}
```

```json
{"op": "report_constant_input_gates", "args": {"gate_type": "nand"}}
```

`gate_type` is optional and may be null.

```json
{"op": "replace_nand_const1_with_not", "args": {}}
```

```json
{"op": "constant_propagation", "args": {}}
```

```json
{"op": "rename_net", "args": {"old_net": "n1214", "new_net": "renamed_wire"}}
```

```json
{"op": "check_equivalent_to_original", "args": {}}
```

## Release Prompt Coverage From Test32

These test32 prompts are now handled by the local rule/hybrid path:

- Response 4:
  - Prompt: determine whether gate `g0` lies on any maximum-depth path.
  - Operation: `gate_on_max_depth_path`.

- Response 5:
  - Prompt: determine the number of primary inputs and outputs.
  - Operation: `report_io_counts`.

- Response 6:
  - Prompt: list register-to-register paths through combinational logic.
  - Operation: `report_register_paths`.

- Response 7:
  - Prompt: replace 2-input NAND gates with one constant-1 input by inverters.
  - Operation: `replace_nand_const1_with_not`.

- Response 10:
  - Prompt: rename wire `n1214` to `renamed_wire`.
  - Operation: `rename_net`.

- Response 11:
  - Prompt: verify equivalence against original loaded netlist.
  - Operation: `check_equivalent_to_original`.

- Response 12:
  - Prompt: transitive fanout of primary input `n0`.
  - Operation: `report_fanout_cone`.

- Response 16:
  - Prompt: report NAND gates with constant inputs.
  - Operation: `report_constant_input_gates`.

- Responses 17 and 18:
  - Prompt: simplify/propgate constants and report eliminated NAND gates.
  - Current operation: `constant_propagation`.
  - Limitation: the response reports total changed gates, not a dedicated
    "NAND eliminated since last report" counter.

## Verification Results

Commands that passed after these changes:

```powershell
python -m unittest discover -s tests
```

Result:

```text
Ran 93 tests
OK
```

```powershell
python scripts/run_release_testcases.py --case test32 --planner rule --fail-on-error --fail-on-unsupported --fail-on-response-mismatch --timeout 300
```

Result:

```text
test32: rc=0, prompts=20, responses=20, missing=0, unsupported=0, errors=0
```

```powershell
python scripts/run_release_testcases.py --case test32 --planner hybrid --fail-on-error --fail-on-unsupported --fail-on-response-mismatch --timeout 300
```

Result:

```text
test32: rc=0, prompts=20, responses=20, missing=0, unsupported=0, errors=0
```

## Dependency Note

`z3-solver` is required for large release-case equivalence checks. Without it,
large transforms such as `rename_net` may fail with the brute-force fallback
limit. `requirements.txt` already includes:

```text
z3-solver>=4.12
```

## Backend Notes For Review

- Transactional transforms still run on a copied design.
- Connectivity checking is now baseline-aware: if the loaded design already has
  missing drivers, a transform may commit only when it does not introduce new
  missing or duplicate drivers.
- Equivalence remains combinational-only; DFFs are boundaries.
- `replace_nand_const1_with_not` is a local Boolean identity rewrite and keeps
  the original instance name and output net.
- `report_register_paths` caps output with `max_paths` to avoid exploding
  release responses.

## Remaining Issues Not Solved By Tool Schema

- Full all-path enumeration is still not implemented.
- Cone-local gate type counts are still pending.
- Full NAND/NOT or ABC/Yosys technology mapping is still pending.
- "How many NAND gates were eliminated by constant propagation?" currently maps
  to `constant_propagation`, but a more precise delta counter may be needed for
  exact answer quality.
