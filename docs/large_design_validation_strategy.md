# Large Design Validation Strategy

This document records how the release-output validator checks large circuit
responses and how we convert optimization results into reportable numbers.

## Goal

For small designs, the validator can usually re-run the exact analysis or run
full before/after equivalence. Large designs need a bounded policy: the
validator should still catch clear wrong answers, but it must not claim a full
proof when the proof is too expensive for the available time.

The validator therefore uses three verdicts:

- `PASS`: an external oracle or formal/structural check confirms the response.
- `FAIL`: an external oracle finds a contradiction, runtime error, or broken
  transformation.
- `INCONCLUSIVE`: the validator does not have enough bounded evidence to prove
  correctness. This is not counted as a correct answer.

## Current Thresholds

- Full generic transform equivalence is attempted when the larger before/after
  design has at most `4000` gates/DFFs.
- Expensive read-only analysis validators use a separate `20000` gate/DFF
  guard.
- Large transform selected-output equivalence is limited to at most `8`
  explicitly selected primary outputs.

These thresholds are engineering budgets, not contest rules. They can be tuned
after measuring runtime on the official machine.

## Large Transform Validation Flow

For a transformation response whose before/after snapshots are larger than the
full equivalence threshold, the validator now applies this layered policy:

1. Parse both ledger snapshots. If before/after snapshots are missing, return
   `INCONCLUSIVE`.
2. Run operation-specific residual checks when available.
   - Example: a full XOR replacement should leave zero XOR gates.
   - Example: bounded fanout insertion that skipped nets remains
     `INCONCLUSIVE`.
3. Run connectivity on the transformed design.
   - Missing drivers or duplicate drivers cause `FAIL`.
   - This catches broken net rewrites even when full equivalence is too costly.
4. If the prompt/plan names an explicit primary output through `target`, `dst`,
   `output`, or `outputs`, run equivalence only for those selected outputs.
   - For constrained cone optimization on a DFF-Q target, resolve the target
     to the D-input combinational cone and prove that next-state function.
   - If selected-output equivalence passes, return `PASS`.
   - If selected-output equivalence fails, return `FAIL`.
   - If the mismatch depends on an unknown/X constant, return `INCONCLUSIVE`.
5. If no selected primary output is available, return `INCONCLUSIVE` after
   connectivity passes. The validator records that the design is structurally
   valid but not fully proven equivalent.

This makes the large-design policy conservative: it can prove targeted
transformations, reject broken outputs, and avoid false `PASS` on whole-design
rewrites that are too expensive to prove.

## Exact Compositional Certificates

Some large transforms can be proven without solving the entire Boolean
network:

- A single-net buffer tree is valid only if every added gate is a one-input
  BUF, every BUF output traces to the requested source net, contracting those
  BUF identities reconstructs the before design exactly, and every tree driver
  meets the requested fanout.
- A whole-design buffer forest applies the same proof to multiple original
  root nets, then checks the fanout bound over the complete transformed design.
- Degenerate `AND(x,x)`, `OR(x,x)`, and BUF removals are validated by
  recursively contracting removed outputs to their identity roots and
  requiring exact reconstruction of the transformed design.
- Net and gate rename operations use alpha-equivalence certificates.
- Wire-only dangling cleanup requires identical non-wire source text and
  identical parsed logic after unused declarations are removed.

These are complete proofs for the stated structural rewrite, not heuristic
connectivity checks.

For expression equivalence, ABC receives the union of variables used by both
expressions as a common primary-input universe. Z3 is also evaluated; a
concrete Z3 counterexample overrides an ABC equivalent verdict. This prevents
positional PI matching from declaring expressions over different signals
equivalent.

## Exact Path Counts

For an acyclic combinational source-to-destination region, the validator counts
paths with dynamic programming over the relevant DAG. This computes an exact
integer without materializing every path.

- Results up to 10,000 paths may be materialized in memory. Larger acyclic
  results up to 300,000 paths are streamed directly to a report artifact.
- The validator independently streams the same deterministic path order and
  compares every report line without loading the complete artifact.
- If the exact count is much larger, the validator checks the bounded prefix
  and reports the exact total, but leaves the answer `INCONCLUSIVE` because the
  user requested a complete listing.
- A cycle in the relevant region remains `INCONCLUSIVE`; DAG path counting
  cannot define a finite complete path set in that case.

## Optimization Metrics

The validator supports a CSV metrics export:

```powershell
python scripts\validate_release_outputs.py --planner llm_openai --all `
  --output outputs\validator_results_llm_openai.jsonl `
  --metrics-output outputs\validator_metrics_llm_openai.csv
```

The CSV records each transform/optimization response with:

- testcase and response id
- operation name
- validation status and detail
- before/after gate count and gate delta
- before/after depth and depth delta
- final max fanout when available
- number of changed/skipped items when available
- inferred cost objective: `gate_count`, `max_logic_depth`, `max_fanout`, or
  `structural_change`

For report numbers, count a QoR improvement as successful only when:

1. `validation_status == PASS`, and
2. at least one target metric improves, such as `gate_delta < 0` or
   `depth_delta < 0`.

Examples from the current local sample:

- `test25 response 8`: depth improves from `49` to `44`, but equivalence is
  `INCONCLUSIVE` because the testcase contains unknown/X behavior. Report this
  as "measured QoR improvement, not externally proven."
- `test26 response 7`: gates improve from `3485` to `2431`, but depth worsens
  from `94` to `102` and equivalence is `FAIL`. Report this as a rejected
  optimization, not a successful improvement.

## Why This Is Reportable

The validator uses an external evidence chain rather than trusting the agent's
own response text:

- release runner ledger snapshots preserve the before/after netlists;
- parser reloads those snapshots independently;
- analysis answers are recomputed from the parsed design;
- transformations are checked by equivalence, connectivity, and residual
  properties;
- optimization numbers are extracted from ledger deltas and tied to the
  validation verdict.

Therefore the report can separate three kinds of results:

- functionally validated improvements;
- measured but inconclusive improvements;
- rejected transformations/optimizations.

This distinction is important for large circuits because bounded validation is
honest about what was proven and what still needs stronger backend support,
such as ABC/Yosys cone-level equivalence or partitioned whole-design checking.

## Next Improvements

- Add an ABC/Yosys selected-cone equivalence backend for large designs so the
  selected-output check can scale beyond the current expression/Z3 path.
- Add partitioned equivalence for whole-design transformations: split primary
  outputs into batches and stop on the first failing batch.
- Add timeout-aware validator logging so every `INCONCLUSIVE` records whether
  it came from missing target, bounded skip, X semantics, or backend timeout.
- Add aggregate report generation from the metrics CSV, including counts of
  validated gate reductions, validated depth reductions, and rejected QoR
  claims.
