from __future__ import annotations

import argparse # read command line argument
import sys      # read natural language request from stdin

from agent.planner import plan_request          # turn natural language into tool call
from runtime.config import load_config          # 
from runtime.dispatcher import dispatch_plan    # do tool call
from runtime.response import emit_response      # wrapping result
from runtime.state import CurrentState          # including state.design/previous_results.etc


def main() -> int:
    """Run the stdin-driven contest request loop."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-config", dest="config", required=False)
    args = parser.parse_args()

    config = load_config(args.config)
    state = CurrentState(config=config)

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
