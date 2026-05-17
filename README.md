# CADA1070 Alpha - Day1 Skeleton

This repository is the Day1 baseline for the ICCAD Contest Problem A style project:
LLM-assisted netlist exploration and transformation.

## Day1 Goals

By the end of Day1, the project should have:

- A fixed internal representation (IR) for gate-level Verilog netlists.
- A fixed EDA Tool API that the LLM agent is allowed to call.
- A fixed runtime state object for multi-turn testcase execution.
- A fixed stdout/log response format.
- A minimal CLI skeleton that can read requests from stdin and emit `#RESPONSE N ... #END N`.
- A folder structure that allows three people to work in parallel.

## Run Smoke Test

```bash
chmod +x cada1070_alpha
./cada1070_alpha -config config.example.yaml < tests/smoke_input.txt
```

Expected behavior:

- The program reads each line from stdin.
- It prints responses wrapped by `#RESPONSE <id>` and `#END <id>`.
- It creates a log file under `output/logs/` when a testcase begins.

## Main Modules

```text
agent/      LLM planner, prompt, JSON plan parser
eda/        IR, graph, analysis, transform, verification
parser/     Verilog parser
runtime/    state, dispatcher, response formatter, config loader
docs/       fixed specifications
tests/      smoke tests and small netlists
output/     generated logs and output netlists
```

## Day1 Division

- Person A, data-structure background: `eda/design.py`, parser interface, graph interface.
- Person B, ML background: `agent/`, JSON tool schema, prompt.
- Person C, IC contest background: `runtime/`, CLI, logging, transform/verify interface.
