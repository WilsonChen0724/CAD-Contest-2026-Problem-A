# Local Optimization Research Plan

## Gate Replacement Coverage

Current `eda/transform.py` does not implement every possible source-gate to
target-gate conversion pair. It implements the combinations needed by observed
release prompts and safe contest workflows:

- all primitive gates -> AND/NOT via `replace_with_and_not`
- AND/NOT -> NAND via `replace_and_not_with_nand`
- XOR -> NAND via `replace_xor_with_nand`
- XNOR -> NOR via `replace_xnor_with_nor`
- OR in one cone -> NAND/NOT via `replace_or_with_nand_not`
- NAND with constant 1 -> NOT via `replace_nand_const1_with_not`
- constant/redundant input simplification via `constant_propagation`

A complete pairwise conversion matrix would be larger than we need and can make
LLM tool selection less stable. A better design is a small number of target
library mappers:

- `remap_to_and_not`
- `remap_to_nand_only`
- `remap_to_nor_only`
- `remap_cone_to_library(target, allowed_gates)`

These cover most pairwise requests while keeping the tool surface compact.

## When Yosys/ABC Can Be Weak For Small Circuits

Yosys/ABC is strong as a general synthesis backend, but for this contest there
are several cases where a local optimizer can be better or safer:

1. Strict library-preserving prompts
   - Example: keep a cone NAND/NOT-only or AND/NOT-only.
   - ABC may optimize depth but reintroduce gate types we do not want, requiring
     another remap that can increase depth again.

2. Simple associative chains
   - AND/OR/XOR chains can often be balanced locally with predictable depth
     improvement.
   - ABC may choose a different technology structure that is not better under
     our structural depth metric.

3. Constant and redundant input cleanup
   - Local rules are exact, fast, and easy to verify.
   - Running Yosys for this is unnecessary overhead on small designs.

4. Contest-output stability
   - ABC may rename many nets/gates or produce structures our parser accepts but
     our structural guards reject.
   - Local transforms preserve naming and modify only narrow regions.

## Candidate Algorithms For This Project

Priority order for local algorithms:

1. Associative tree balancing
   - Flatten safe AND/OR/XOR fanin chains.
   - Rebuild as a balanced tree.
   - Cost: depth first, gate count second.

2. Target-library structural remapping
   - Convert a cone or whole design into AND/NOT, NAND-only, or NOR-only.
   - Prefer target-library preservation over aggressive area reduction.

3. Algebraic local rewrite rules
   - Idempotent: `a & a = a`, `a | a = a`.
   - Complement: `a & ~a = 0`, `a | ~a = 1` when direct inverter relation is visible.
   - Absorption: `a | (a & b) = a`, `a & (a | b) = a` for small obvious patterns.

4. Structural hashing / equivalent gate merge
   - Already partially implemented as `merge_equivalent_gates`.
   - Can be extended to normalize inversions and simple De Morgan forms.

5. Bounded cut rewriting
   - Enumerate small 3- to 5-input cones.
   - Try pre-defined equivalent implementations and select by cost.
   - More powerful but higher implementation risk.

## Experiment Folder

The first experimental implementation is in:

- `experiments/local_opt/local_rewriters.py`

It currently includes:

- `rebalance_associative_chains`
- `run_local_small_design_optimization`

This should stay outside the official runtime until we collect enough comparison
results against Yosys/ABC.

## Suggested Benchmark Flow

1. Pick small and medium cases:
   - small: test22, test23, test25
   - medium: test26, test27, test29, test30

2. For each case, compare:
   - original gate count and depth
   - local optimizer gate count and depth
   - Yosys/ABC gate count and depth
   - runtime
   - whether output still satisfies gate-library constraints

3. Promote only stable local passes into `eda/transform.py` after adding tests.
