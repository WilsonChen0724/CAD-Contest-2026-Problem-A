from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from eda.design import DFF, Design, Gate
from eda.verify import check_fanout
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

    def test_dispatcher_reports_all_paths_and_gate_type_count(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        state.design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        state.design.add_gate(Gate(name="U2", type="not", inputs=["n1"], output="dst"))
        state.design.add_gate(Gate(name="U_bypass", type="buf", inputs=["src"], output="dst"))

        paths = dispatch_plan(state, {"op": "report_all_paths", "args": {"src": "src", "dst": "dst"}})
        count = dispatch_plan(state, {"op": "report_gate_type_count", "args": {"gate_type": "not"}})

        self.assertIn('Combinational paths from "src" to "dst": 2', paths)
        self.assertIn("U_bypass", paths)
        self.assertIn("NOT gate count: 1", count)
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

    def test_dispatcher_reports_structural_gate_data(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"y"})
        state.design.add_gate(Gate(name="U0", type="and", inputs=["a", "b"], output="n1"))
        state.design.add_gate(Gate(name="U1", type="buf", inputs=["n1"], output="y"))
        state.design.add_dff(DFF(name="FF0", d="n1", q="q0", clk="clk"))

        counts = dispatch_plan(state, {"op": "report_gate_counts", "args": {}})
        fanout = dispatch_plan(state, {"op": "report_fanout", "args": {"net": "n1"}})
        gate_fanout = dispatch_plan(state, {"op": "report_fanout", "args": {"net": "U0"}})
        connections = dispatch_plan(state, {"op": "report_gate_connections", "args": {"gate": "U0"}})

        self.assertIn("- and: 1", counts)
        self.assertIn("- buf: 1", counts)
        self.assertIn("- dff: 1", counts)
        self.assertIn('Fanout of net "n1"', fanout)
        self.assertIn("2 load(s)", fanout)
        self.assertIn('Fanout of gate "U0"', gate_fanout)
        self.assertIn("2 load(s)", gate_fanout)
        self.assertIn("U1", fanout)
        self.assertIn("FF0", fanout)
        self.assertIn('Gate "U0": type=and', connections)
        self.assertIn("GATE:U1", connections)

    def test_dispatcher_checks_current_design_against_original_snapshot(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        state.design.add_gate(Gate(name="U0", type="and", inputs=["a", "b"], output="y"))
        state.original_design = deepcopy(state.design)

        equivalent = dispatch_plan(state, {"op": "check_equivalent_to_original", "args": {}})
        state.design.gates["U0"].type = "or"
        not_equivalent = dispatch_plan(state, {"op": "check_equivalent_to_original", "args": {}})

        self.assertIn("Equivalent to original loaded netlist", equivalent)
        self.assertIn("Not equivalent to original loaded netlist", not_equivalent)

    def test_dispatcher_formats_constant_equivalence_as_yes_no(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"z"})
        state.design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="z"))

        body = dispatch_plan(
            state,
            {"op": "check_equivalence", "args": {"expr": "0", "target": "z"}},
        )

        self.assertIn('No. "z" is not always 0.', body)
        self.assertIn("Counterexample:", body)

    def test_dispatcher_renames_net_transactionally(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n_mid"))
        state.design.add_gate(Gate(name="U1", type="not", inputs=["n_mid"], output="y"))

        body = dispatch_plan(
            state,
            {"op": "rename_net", "args": {"old_net": "n_mid", "new_net": "renamed_mid"}},
        )

        self.assertIn('Renamed net "n_mid" to "renamed_mid"', body)
        self.assertEqual(state.design.gates["U1"].inputs, ["renamed_mid"])
        self.assertNotIn("n_mid", state.design.wires)

    def test_dispatcher_renames_gate_transactionally(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))

        body = dispatch_plan(
            state,
            {"op": "rename_gate", "args": {"old_name": "U0", "new_name": "U_RENAMED"}},
        )

        self.assertIn('Renamed gate instance "U0" to "U_RENAMED"', body)
        self.assertIn("U_RENAMED", state.design.gates)
        self.assertNotIn("U0", state.design.gates)
        self.assertEqual(state.design.gates["U_RENAMED"].name, "U_RENAMED")
    def test_dispatcher_propagates_constants_transactionally(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_and", type="and", inputs=["a", "1'b1"], output="n_mid"))
        state.design.add_gate(Gate(name="U_or", type="or", inputs=["n_mid", "1'b0"], output="y"))

        body = dispatch_plan(state, {"op": "constant_propagation", "args": {}})

        self.assertIn("Propagated constants", body)
        self.assertNotIn("U_and", state.design.gates)
        self.assertEqual(state.design.gates["U_or"].type, "buf")
        self.assertEqual(state.design.gates["U_or"].inputs, ["a"])

    def test_transform_allows_preexisting_connectivity_issues_without_new_regression(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        state.design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n_mid"))
        state.design.add_gate(Gate(name="U1", type="not", inputs=["n_mid"], output="y"))
        state.design.wires.add("preexisting_missing")

        body = dispatch_plan(
            state,
            {"op": "rename_net", "args": {"old_net": "n_mid", "new_net": "renamed_mid"}},
        )

        self.assertIn('Renamed net "n_mid" to "renamed_mid"', body)
        self.assertEqual(state.design.gates["U1"].inputs, ["renamed_mid"])
        self.assertIn("preexisting_missing", state.design.wires)

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

    def test_last_transform_stats_reports_delta(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1", "y2"})
        for index in range(3):
            state.design.add_gate(Gate(name=f"U{index}", type="buf", inputs=["src"], output=f"y{index}"))

        dispatch_plan(state, {"op": "insert_buffers_for_fanout", "args": {"net": "src", "max_fanout": 2}})
        body = dispatch_plan(state, {"op": "report_last_transform_stats", "args": {}})

        self.assertIn('Last transform "insert_buffers_for_fanout" stats:', body)
        self.assertIn("gate type delta", body)
        self.assertIn("transform-reported inserted BUF gates", body)
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

    def test_dispatcher_inserts_fanout_buffers_with_post_check(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src"}, outputs={f"y{i}" for i in range(5)})
        for i in range(5):
            state.design.add_gate(Gate(name=f"U{i}", type="buf", inputs=["src"], output=f"y{i}"))

        body = dispatch_plan(
            state,
            {"op": "insert_buffers_for_fanout", "args": {"net": "src", "max_fanout": 2}},
        )

        self.assertIn("Inserted", body)
        self.assertIn('Final fanout of "src" is 2', body)
        self.assertTrue(check_fanout(state.design, 2)["ok"])

    def test_dispatcher_allows_preexisting_fanout_violations_on_other_nets(self) -> None:
        outputs = {f"y{i}" for i in range(5)} | {f"z{i}" for i in range(3)}
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src", "other"}, outputs=outputs)
        for i in range(5):
            state.design.add_gate(Gate(name=f"U_src_{i}", type="buf", inputs=["src"], output=f"y{i}"))
        for i in range(3):
            state.design.add_gate(Gate(name=f"U_other_{i}", type="buf", inputs=["other"], output=f"z{i}"))

        body = dispatch_plan(
            state,
            {"op": "insert_buffers_for_fanout", "args": {"net": "src", "max_fanout": 2}},
        )

        self.assertIn("Inserted", body)
        fanout = check_fanout(state.design, 2)
        self.assertNotIn("src", fanout["violations"])
        self.assertEqual(fanout["violations"].get("other"), 3)

    def test_dispatcher_balances_depths_with_post_check(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1"})
        state.design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        state.design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        state.design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y1"))

        body = dispatch_plan(
            state,
            {"op": "balance_depth_with_buffers", "args": {"src": "src", "dsts": ["y0", "y1"]}},
        )

        self.assertIn("Inserted 1 buffer", body)
        self.assertIn("Final depths", body)

    def test_dispatcher_optimizes_cone_with_depth_guard(self) -> None:
        state = CurrentState()
        state.design = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        state.design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="n_buf"))
        state.design.add_gate(Gate(name="U_not0", type="not", inputs=["n_buf"], output="n_inv"))
        state.design.add_gate(Gate(name="U_not1", type="not", inputs=["n_inv"], output="n_clean"))
        state.design.add_gate(Gate(name="U_and", type="and", inputs=["n_clean", "b"], output="y"))

        body = dispatch_plan(
            state,
            {"op": "optimize_cone", "args": {"target": "y", "max_depth": 1, "minimize_gate_count": True}},
        )

        self.assertIn("Optimized cone", body)
        self.assertIn("4 -> 1 gate", body)
        self.assertEqual(set(state.design.gates), {"U_and"})

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
