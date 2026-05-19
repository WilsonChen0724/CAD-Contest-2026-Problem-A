from __future__ import annotations

import re

from runtime.state import CurrentState

def format_response(response_id: int, body: str) -> str:
    """Wrap one response body with the contest-required tags."""
    clean_body = _sanitize_body(body)
    return f"#RESPONSE {response_id}\n{clean_body}\n#END {response_id}"


def emit_response(state: CurrentState, body: str) -> str:
    """Format a response, append it to the active log, and return it."""
    response_id = state.next_response_id()
    block = format_response(response_id, body)

    if state.log_path is not None:
        with state.log_path.open("a", encoding="utf-8") as f:
            f.write(block + "\n")

    return block


def _sanitize_body(body: str) -> str:
    """Normalize body text and prevent nested response tags."""
    text = str(body).strip() if body is not None else ""
    if not text:
        text = "No response body was produced."
    text = re.sub(r"(?m)^#RESPONSE\b", "[RESPONSE]", text)
    text = re.sub(r"(?m)^#END\b", "[END]", text)
    return text
