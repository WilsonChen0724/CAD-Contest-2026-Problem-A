from __future__ import annotations

import unittest

from runtime.dispatcher import dispatch_plan
from runtime.state import CurrentState


class DispatcherTest(unittest.TestCase):
    def test_dispatcher_accepts_checked_unsupported_plan(self) -> None:
        body = dispatch_plan(
            CurrentState(),
            {"op": "unsupported", "args": {"reason": "ambiguous request"}},
        )

        self.assertIn("could not map", body)
        self.assertIn("ambiguous request", body)


if __name__ == "__main__":
    unittest.main()
