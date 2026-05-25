from __future__ import annotations

import unittest

from eda.design import DFF, Design, Gate
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

    def test_dispatcher_reports_all_paths_pass_through(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        state.design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        state.design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="dst"))

        body = dispatch_plan(
            state,
            {"op": "all_paths_pass_through", "args": {"src": "src", "dst": "dst", "node": "U1"}},
        )

        self.assertIn("Yes", body)
        self.assertIn("passes through", body)

    def test_dispatcher_reports_outputs_by_cone_size(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1"})
        state.design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="n1"))
        state.design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y0"))
        state.design.add_gate(Gate(name="U3", type="buf", inputs=["a"], output="y1"))

        body = dispatch_plan(
            state,
            {"op": "report_outputs_by_cone_size", "args": {"min_gates": 1}},
        )

        self.assertIn("y0: 2 gates", body)
        self.assertNotIn("y1: 1 gates", body)

    def test_dispatcher_reports_same_clock_domain(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"clk", "clk2"}, outputs={"out"})
        state.design.add_dff(DFF(name="FF0", d="d0", q="q0", clk="clk"))
        state.design.add_dff(DFF(name="FF1", d="d1", q="q1", clk="clk"))
        state.design.add_dff(DFF(name="FF2", d="d2", q="q2", clk="clk2"))

        same = dispatch_plan(
            state,
            {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF1"}},
        )
        different = dispatch_plan(
            state,
            {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF2"}},
        )

        self.assertIn("Yes", same)
        self.assertIn("No", different)


if __name__ == "__main__":
    unittest.main()
