from __future__ import annotations

import unittest
from unittest.mock import patch

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

    def test_dispatcher_runs_new_transformations(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        state.design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="n_or"))
        state.design.add_gate(Gate(name="U_or", type="or", inputs=["n_or", "b"], output="y"))
        state.design.add_gate(Gate(name="U_dead", type="buf", inputs=["b"], output="dead"))

        collapse = dispatch_plan(state, {"op": "replace_inv_buf_with_inv", "args": {}})
        rewrite = dispatch_plan(state, {"op": "replace_or_with_nand_not", "args": {"cone_target": "y"}})
        cleanup = dispatch_plan(state, {"op": "remove_dangling", "args": {}})

        self.assertIn("1 inverter-buffer", collapse)
        self.assertIn("1 OR gate", rewrite)
        self.assertIn("U_dead", cleanup)
        self.assertNotIn("U_dead", state.design.gates)

    def test_transform_rejects_invalid_candidate_without_polluting_state(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="y"))

        with self.assertRaisesRegex(RuntimeError, "Transformation rejected"):
            dispatch_plan(
                state,
                {"op": "replace_buffers_with_and", "args": {"targets": ["U_buf"], "extra_input": "missing_ctrl"}},
            )

        self.assertEqual(state.design.gates["U_buf"].type, "buf")
        self.assertEqual(state.design.gates["U_buf"].inputs, ["a"])

    def test_function_preserving_transform_rejects_equivalence_failure(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="y"))

        def bad_transform(design):
            design.gates["U_buf"].type = "not"
            return {"changed": ["U_buf"], "num_changed": 1}

        with patch("runtime.dispatcher.replace_inv_buf_with_inv", side_effect=bad_transform):
            with self.assertRaisesRegex(RuntimeError, "equivalence check failed"):
                dispatch_plan(state, {"op": "replace_inv_buf_with_inv", "args": {}})

        self.assertEqual(state.design.gates["U_buf"].type, "buf")
        self.assertEqual(state.design.gates["U_buf"].inputs, ["a"])

    def test_multi_step_transform_plan_operates_on_evolving_state(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        state.design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="n_or"))
        state.design.add_gate(Gate(name="U_or", type="or", inputs=["n_or", "b"], output="y"))
        state.design.add_gate(Gate(name="U_dead", type="buf", inputs=["b"], output="dead"))

        body = dispatch_plan(
            state,
            {
                "steps": [
                    {"op": "replace_inv_buf_with_inv", "args": {}},
                    {"op": "replace_or_with_nand_not", "args": {"cone_target": "y"}},
                    {"op": "remove_dangling", "args": {}},
                    {"op": "check_connectivity", "args": {}},
                ]
            },
        )

        self.assertIn("Replaced 1 inverter-buffer", body)
        self.assertIn("Replaced 1 OR gate", body)
        self.assertIn("'ok': True", body)
        self.assertNotIn("U_dead", state.design.gates)
        self.assertEqual(state.design.gates["U_buf"].type, "not")
        self.assertEqual(state.design.gates["U_or"].type, "nand")

    def test_dispatcher_runs_formal_checks(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"z"})
        state.design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="z"))

        equivalence = dispatch_plan(
            state,
            {"op": "check_equivalence", "args": {"expr": "a & b", "target": "z"}},
        )
        prop = dispatch_plan(
            state,
            {"op": "check_property", "args": {"target": "z", "property": "z -> a"}},
        )

        self.assertIn("Equivalent", equivalence)
        self.assertIn("Property holds", prop)

    def test_dispatcher_formats_formal_counterexamples(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"z"})
        state.design.add_gate(Gate(name="U1", type="or", inputs=["a", "b"], output="z"))

        equivalence = dispatch_plan(
            state,
            {"op": "check_equivalence", "args": {"expr": "a & b", "target": "z"}},
        )
        prop = dispatch_plan(
            state,
            {"op": "check_property", "args": {"target": "z", "property": "z -> (a & b)"}},
        )

        self.assertIn("Not equivalent", equivalence)
        self.assertIn("Counterexample:", equivalence)
        self.assertIn("Property does not hold", prop)
        self.assertIn("Counterexample:", prop)

    def test_formal_multi_step_plan_reads_like_end_to_end_flow(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"z"})
        state.design.add_gate(Gate(name="U1", type="and", inputs=["a", "b"], output="z"))

        body = dispatch_plan(
            state,
            {
                "steps": [
                    {"op": "check_equivalence", "args": {"expr": "a & b", "target": "z"}},
                    {"op": "check_property", "args": {"target": "z", "property": "z -> a"}},
                ]
            },
        )

        self.assertIn("Equivalent", body)
        self.assertIn("Property holds", body)


if __name__ == "__main__":
    unittest.main()
