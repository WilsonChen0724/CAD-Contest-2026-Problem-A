# P1 Completion Matrix

Date: 2026-07-27

This matrix freezes the beta P1 scope from:

- `docs/current_progress_and_next_features.md`
- `docs/work_division.md`
- `docs/release_testcase_todo.md`

Completion is counted by capability, not by testcase count. An item is complete
only when the backend behavior, dispatcher/planner exposure where applicable,
focused tests, and release-ledger evidence all exist.

| ID | P1 capability | Status | Authoritative evidence |
| --- | --- | --- | --- |
| P1-01 | Register-path scopes: DFF-to-DFF, PI-to-DFF, DFF-to-PO | Complete | `register_to_register_paths`, dispatcher formatting, planner/schema exposure |
| P1-02 | Floating and unconnected signal reporting | Complete | connectivity oracle, floating placeholder handling, dispatcher/validator tests |
| P1-03 | Cut and articulation structural reports | Complete | `cut_signal_between_pi_po`, `articulation_points_between`, external validator checks |
| P1-04 | Fixed-point, bounded constant propagation | Complete | transactional transform, gate-level rewrites, delta reporting, transform tests |
| P1-05 | Domain-specific previous-transform statistics | Complete | gate/DFF/net/type deltas plus transform-reported buffer/removal/merge counts |
| P1-06 | LLM semantic guards and paraphrase coverage | Complete | plan checker intent classes, rule-planner mappings, planner/schema tests |
| P1-07 | Scalable external formal validation | Complete | Z3 full/selected-output checks, ABC cross-check, structural certificates |
| P1-08 | Large report and QoR validation | Complete | streamed exact path artifacts, line-by-line oracle, JSONL/CSV QoR metrics |
| P1-09 | Duplicate-driver-safe destructive transforms | Complete | full `driver_lists`, unique-driver guards, duplicate-driver transform regressions |
| P1-10 | Reproducible validator provenance | Complete | Every JSONL verdict records ledger, before/after snapshot, and validator-source SHA-256 values |

Current completion: **10 of 10 capabilities (100%)**.

## P0 Baseline

The 2026-07-27 full LLM/OpenAI ledger validation is:

```text
Validated 459 response(s): PASS=459, FAIL=0, SKIP=0, INCONCLUSIVE=0
Metrics: 68 transform/optimization response(s),
         gate improvements=15,
         depth improvements=2,
         validated improvements=16
```

The generated JSONL/CSV files remain under `outputs/` and are excluded from
source commits.

## P1 Exit Evidence

- Unit tests: `286 tests`, all PASS.
- Final full LLM/OpenAI ledger validation:
  `PASS=459`, `FAIL=0`, `SKIP=0`, `INCONCLUSIVE=0`.
- Provenance audit:
  - 459/459 verdicts contain a valid ledger SHA-256.
  - 459/459 verdicts contain one common validator-source SHA-256.
  - The results identify 40 distinct testcase ledgers.
  - 419 after-snapshot and 379 before-snapshot hashes are present; the missing
    values correspond to lifecycle records that have no such snapshot.
- A direct recomputation for `test38 response 14` matched all four recorded
  ledger, before-snapshot, after-snapshot, and validator hashes.

Focused duplicate-driver rule-mode result:

```text
Validated 96 response(s): PASS=96, FAIL=0, SKIP=0, INCONCLUSIVE=0
Metrics: 28 transform/optimization response(s),
         gate improvements=7,
         depth improvements=1,
         validated improvements=7
```

`test29 response 8` uses an exact compositional certificate for 15
double-inverter contractions. This replaces a selected-output Z3 attempt that
exceeded 300 seconds.
