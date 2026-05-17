from __future__ import annotations

from runtime.state import CurrentState


def format_response(response_id: int, body: str) -> str:
    return f"#RESPONSE {response_id}\n{body.strip()}\n#END {response_id}"


def emit_response(state: CurrentState, body: str) -> str:
    response_id = state.next_response_id()
    block = format_response(response_id, body)

    if state.log_path is not None:
        with state.log_path.open("a") as f:
            f.write(block + "\n")

    return block
