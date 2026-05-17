# Day1 Work Division

## Person A: EDA Core / Data Structure

Owner files:

```text
eda/design.py
eda/graph.py
parser/verilog_parser.py
```

Day1 tasks:

- Define `Gate`, `DFF`, `Design`.
- Define driver/fanout convention.
- Create parser function signature.
- Create graph rebuild function signature.

Day1 done when:

- `from eda.design import Design, Gate, DFF` works.
- A dummy design can add gates and rebuild driver/fanout tables.

## Person B: LLM Agent / Tool Planner

Owner files:

```text
agent/planner.py
agent/llm_api.py
agent/prompt.txt
docs/tool_spec.md
```

Day1 tasks:

- Define JSON tool-call format.
- Write prompt rules.
- Implement a temporary rule-based planner for smoke test.
- Keep LLM API as stub.

Day1 done when:

- Natural-language smoke commands can produce tool dictionaries.
- Planner output validates as Python dict.

## Person C: Runtime / Integration / Verification Interface

Owner files:

```text
main.py
cada0001_alpha
runtime/state.py
runtime/dispatcher.py
runtime/response.py
runtime/config.py
eda/transform.py
eda/verify.py
```

Day1 tasks:

- Implement CLI.
- Implement stdin loop.
- Implement response wrapper.
- Implement testcase log creation.
- Implement dispatcher skeleton.

Day1 done when:

- `./cada0001_alpha -config config.example.yaml < tests/smoke_input.txt` runs.
- stdout uses `#RESPONSE N` and `#END N`.
- log file is generated for a begun testcase.
