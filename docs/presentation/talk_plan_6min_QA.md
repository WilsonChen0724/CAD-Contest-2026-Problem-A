# CAD_A 6-Minute Talk Plan And QA Notes

Audience: contest judges / technical reviewers  
Speaker target: 6 minutes presentation + 2 minutes QA  
Language style: Chinese explanation with English technical terms

## 6-Minute Flow

### 0:00-0:40 Problem And Goal

Message:

- The task is natural-language EDA control over gate-level Verilog netlists.
- The risk is that an LLM can hallucinate tools, arguments, or unsafe Verilog edits.
- Our goal is a guarded LLM-assisted EDA runtime.

Key sentence:

> We use the LLM only as a planner. All EDA actions are executed by deterministic backend tools.

### 0:40-1:30 Architecture

Show the end-to-end flow:

```text
stdin -> planner -> Tool API -> checker -> dispatcher -> EDA backend -> response
```

Emphasize:

- `main.py` owns the interactive loop.
- `agent/` translates natural language.
- `plan_checker.py` validates the plan.
- `dispatcher.py` executes tools.
- `eda/` owns algorithms and transforms.

### 1:30-2:15 Design IR And Parser

Explain:

- The canonical state is `Design`.
- `Gate` and `DFF` model primitive instances.
- `drivers` and `fanouts` form the graph.
- DFFs are combinational boundaries.
- Yosys is used as a parser/writer helper, not the canonical state.

Why this matters:

- We can debug and verify our own algorithms.
- We can tolerate Yosys edge cases with a direct fallback parser.

### 2:15-3:05 Analysis And Verification

Mention supported analysis:

- path / avoid-path,
- all-path enumeration with output artifact,
- max depth,
- cone reports,
- fanout reports,
- register-path structural reports.

Verification:

- Z3-backed combinational equivalence.
- Connectivity/fanout/depth checks.
- Original netlist snapshot equivalence.

Key sentence:

> Verification is used both as a user-facing tool and as an internal safety guard.

### 3:05-4:15 Transformation And Optimization

Explain transactional transform:

```text
copy design -> transform copy -> rebuild graph -> check guards -> commit or reject
```

Examples:

- rename net/gate,
- constant propagation,
- remove dangling gates,
- fanout buffer insertion,
- depth balancing,
- technology mapping rewrites,
- local cone optimization.

Key sentence:

> A transformation is not committed just because it can be applied. It must pass structural and formal guards.

### 4:15-5:10 Contributions

Main contributions:

- Guarded LLM Tool API architecture.
- Canonical Design IR with deterministic graph algorithms.
- Yosys helper integration with direct fallback.
- Z3 formal verification for equivalence/property checking.
- Transactional transforms for safe optimization.
- Release testcase runner and regression coverage.

Evidence:

- `172 unit tests OK`.
- Latest fixes target real release-case failures: path output size, Windows Yosys path issue, prompt wording, timeout classification.

### 5:10-6:00 Improvements / Research Direction

Keep this short:

- Tighten provider schema from generic `args` to per-op `anyOf`.
- Add optional ABC/Yosys resynthesis backend behind the same verification guards.
- Improve optimization quality and path enumeration scalability.
- Extend formal verification only if sequential multi-cycle requirements appear.

## 2-Minute QA Preparation

### Q1. Why not let LLM directly edit Verilog?

Answer:

Direct editing is hard to validate and easy to hallucinate. We restrict the LLM
to Tool API calls. The backend validates every operation and owns all design
mutation.

### Q2. Why keep your own IR instead of using only Yosys?

Answer:

Yosys is excellent for normalization, but the contest requires custom responses,
stateful transformations, and tool-level validation. Our IR makes graph
algorithms, transactional transforms, and formal guards easier to implement and
explain.

### Q3. Why Z3?

Answer:

Z3 gives exact combinational equivalence/property checking and counterexamples.
This supports both user queries and internal transform safety checks.

### Q4. What does transactional transform mean?

Answer:

We modify a copied design first. The original state is committed only after
connectivity, equivalence, and requested constraints pass. Otherwise the
transform is rejected.

### Q5. Is equivalence sequential?

Answer:

Currently no. Equivalence is combinational-only, and DFFs are boundaries. This
matches the immediate contest requests and keeps checks tractable.

### Q6. How do you handle large path enumeration?

Answer:

We cap response size. Large path listings are written to a report file, while
stdout shows a summary and the first few paths. This avoids timeout and response
explosion.

### Q7. What is the main research/engineering contribution?

Answer:

The contribution is the safety architecture around LLM-assisted EDA: constrained
Tool API planning, deterministic backend execution, canonical IR, formal
verification, and transactional transformation guards.

