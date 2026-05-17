from __future__ import annotations

import argparse
import sys

from agent.planner import plan_request
from runtime.config import load_config
from runtime.dispatcher import dispatch_plan
from runtime.response import emit_response
from runtime.state import CurrentState


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-config", dest="config", required=False)
    args = parser.parse_args()

    _config = load_config(args.config)
    state = CurrentState()

    for raw_line in sys.stdin:
        request = raw_line.strip()
        if not request:
            continue

        try:
            plan = plan_request(request, state)
            body = dispatch_plan(state, plan)
        except Exception as exc:
            body = f"Error: {exc}"

        print(emit_response(state, body), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
