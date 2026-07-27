from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eda.design import DFF, Design, Gate
from eda.graph import rebuild_graph
from eda.analysis import logic_cone, max_depth
from eda.transform import (
    _adaptive_depth_topk,
    _abc_candidate_depth_targets,
    _canonicalize_yosys_generated_names,
    _try_yosys_abc_for_small_cone,
    balance_depth_with_buffers,
    collapse_back_to_back_inverters,
    constant_propagation,
    insert_dedicated_buffers_for_each_load,
    insert_buffers_for_fanout,
    merge_equivalent_gates,
    optimize_cone,
    optimize_design_depth,
    replace_nand_const1_with_not,
    remove_dangling,
    rename_net,
    replace_buffers_with_and,
    replace_and_not_with_nand,
    replace_inv_buf_with_inv,
    replace_or_with_nand_not,
    replace_with_and_not,
    replace_xnor_with_nor,
    replace_xor_with_nand,
)
from eda.verify import check_design_equivalence, check_fanout


class TransformTest(unittest.TestCase):
    def test_replace_buffers_rebuilds_graph(self) -> None:
        design = Design(
            inputs={"a", "ctrl"},
            outputs={"y"},
        )
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        rebuild_graph(design)

        result = replace_buffers_with_and(design, ["U0"], "ctrl")

        self.assertEqual(result["num_changed"], 1)
        self.assertIn("GATE:U0", design.fanouts["ctrl"])

    def test_remove_dangling_removes_logic_not_reaching_outputs(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        design.add_gate(Gate(name="U_dead", type="and", inputs=["a", "b"], output="dead"))
        design.add_gate(Gate(name="U_dead2", type="buf", inputs=["dead"], output="dead2"))

        result = remove_dangling(design)

        self.assertEqual(result["removed_gates"], ["U_dead", "U_dead2"])
        self.assertNotIn("U_dead", design.gates)
        self.assertNotIn("dead", design.wires)
        self.assertIn("PO:y", design.fanouts["y"])

    def test_remove_dangling_keeps_all_drivers_of_live_net(self) -> None:
        design = Design(inputs={"a", "b", "clk"}, outputs={"y"})
        design.add_gate(Gate(name="U_D0", type="buf", inputs=["a"], output="d0"))
        design.add_gate(Gate(name="U_D1", type="not", inputs=["b"], output="d1"))
        design.add_dff(DFF(name="FF0", d="d0", q="q", clk="clk"))
        design.add_dff(DFF(name="FF1", d="d1", q="q", clk="clk"))
        design.add_gate(Gate(name="U_OUT", type="buf", inputs=["q"], output="y"))

        result = remove_dangling(design)

        self.assertEqual(result["num_removed_gates"], 0)
        self.assertEqual(result["num_removed_dffs"], 0)
        self.assertEqual(set(design.dffs), {"FF0", "FF1"})
        self.assertEqual(set(design.gates), {"U_D0", "U_D1", "U_OUT"})

    def test_replace_inv_buf_with_inv_collapses_safe_chain(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="y"))

        result = replace_inv_buf_with_inv(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertNotIn("U_inv", design.gates)
        self.assertNotIn("n_mid", design.wires)
        self.assertEqual(design.gates["U_buf"].type, "not")
        self.assertEqual(design.gates["U_buf"].inputs, ["a"])

    def test_replace_inv_buf_with_inv_skips_shared_intermediate_net(self) -> None:
        design = Design(inputs={"a"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U_inv", type="not", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["n_mid"], output="y"))
        design.add_gate(Gate(name="U_other", type="buf", inputs=["n_mid"], output="z"))

        result = replace_inv_buf_with_inv(design)

        self.assertEqual(result["num_changed"], 0)
        self.assertIn("U_inv", design.gates)
        self.assertEqual(design.gates["U_buf"].type, "buf")

    def test_replace_with_and_not_reconstructs_supported_primitives(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y0", "y1", "y2", "y3", "y4"})
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="y0"))
        design.add_gate(Gate(name="U_nand", type="nand", inputs=["a", "b"], output="y1"))
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y2"))
        design.add_gate(Gate(name="U_xor", type="xor", inputs=["a", "b"], output="y3"))
        design.add_gate(Gate(name="U_xnor", type="xnor", inputs=["a", "b"], output="y4"))
        original = deepcopy(design)

        result = replace_with_and_not(design)

        self.assertEqual(result["num_changed"], 5)
        self.assertTrue(all(gate.type in {"and", "not"} for gate in design.gates.values()))
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_replace_multi_input_and_and_not_with_nand(self) -> None:
        design = Design(inputs={"a", "b", "c"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "b", "c"], output="n"))
        design.add_gate(Gate(name="U_not", type="not", inputs=["n"], output="y"))
        original = deepcopy(design)

        result = replace_and_not_with_nand(design)

        self.assertEqual(result["num_changed"], 2)
        self.assertEqual({gate.type for gate in design.gates.values()}, {"nand"})
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_merge_equivalent_gates_reaches_fixed_point(self) -> None:
        design = Design(inputs={"a", "c"}, outputs={"y"})
        design.add_gate(Gate("A_down1", "and", ["n1", "c"], "d1"))
        design.add_gate(Gate("A_down2", "and", ["n2", "c"], "d2"))
        design.add_gate(Gate("Y_out", "or", ["d1", "d2"], "y"))
        design.add_gate(Gate("Z_up1", "not", ["a"], "n1"))
        design.add_gate(Gate("Z_up2", "not", ["a"], "n2"))
        original = deepcopy(design)

        result = merge_equivalent_gates(design)

        self.assertEqual(result["num_merged"], 2)
        self.assertGreaterEqual(result["passes"], 2)
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_merge_equivalent_gates_batches_gate_and_dff_redirects(self) -> None:
        design = Design(inputs={"a", "b", "clk"}, outputs={"y"})
        design.add_gate(Gate("U0", "and", ["a", "b"], "n0"))
        design.add_gate(Gate("U1", "and", ["b", "a"], "n1"))
        design.add_gate(Gate("U2", "and", ["a", "b"], "n2"))
        design.add_gate(Gate("U_sink", "or", ["n1", "n2"], "sink"))
        design.add_dff(DFF("FF0", d="n2", q="y", clk="clk"))

        result = merge_equivalent_gates(design)

        self.assertEqual(result["num_merged"], 2)
        self.assertEqual(set(design.gates), {"U0", "U_sink"})
        self.assertEqual(design.gates["U_sink"].inputs, ["n0", "n0"])
        self.assertEqual(design.dffs["FF0"].d, "n0")
        self.assertNotIn("n1", design.wires)
        self.assertNotIn("n2", design.wires)

    def test_merge_equivalent_gates_preserves_multiply_driven_output(self) -> None:
        design = Design(inputs={"a", "b", "c"}, outputs={"y"})
        design.add_gate(Gate("U0", "and", ["a", "b"], "n0"))
        design.add_gate(Gate("U1", "and", ["a", "b"], "n1"))
        design.add_gate(Gate("U_multi", "buf", ["c"], "n1"))
        design.add_gate(Gate("U_out", "buf", ["n1"], "y"))

        result = merge_equivalent_gates(design)

        self.assertEqual(result["num_merged"], 0)
        self.assertIn("U1", design.gates)
        self.assertIn("U_multi", design.gates)

    def test_xnor_to_nor_avoids_gate_net_name_collisions(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_xnor", type="xnor", inputs=["a", "b"], output="y"))

        replace_xnor_with_nor(design)

        self.assertFalse(set(design.gates) & design.all_nets())
        self.assertEqual({gate.type for gate in design.gates.values()}, {"nor"})

    def test_xor_to_nand_avoids_gate_net_name_collisions(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_xor", type="xor", inputs=["a", "b"], output="y"))

        replace_xor_with_nand(design)

        self.assertFalse(set(design.gates) & design.all_nets())
        self.assertEqual({gate.type for gate in design.gates.values()}, {"nand"})

    def test_collapse_back_to_back_inverters(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n0"], output="y"))
        before = deepcopy(design)

        result = collapse_back_to_back_inverters(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertNotIn("U0", design.gates)
        self.assertEqual(design.gates["U1"].type, "buf")
        self.assertEqual(design.gates["U1"].inputs, ["a"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_collapse_long_inverter_chain_in_one_batch(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n0"], output="n1"))
        design.add_gate(Gate(name="U2", type="not", inputs=["n1"], output="n2"))
        design.add_gate(Gate(name="U3", type="not", inputs=["n2"], output="y"))
        before = deepcopy(design)

        result = collapse_back_to_back_inverters(design)

        self.assertEqual(result["num_changed"], 2)
        self.assertEqual(set(design.gates), {"U3"})
        self.assertEqual(design.gates["U3"].type, "buf")
        self.assertEqual(design.gates["U3"].inputs, ["a"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_collapse_preserves_inverter_with_shared_output(self) -> None:
        design = Design(inputs={"a"}, outputs={"y", "tap"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="tap"))
        design.add_gate(Gate(name="U1", type="not", inputs=["tap"], output="y"))
        before = deepcopy(design)

        result = collapse_back_to_back_inverters(design)

        self.assertEqual(result["num_changed"], 0)
        self.assertEqual(set(design.gates), {"U0", "U1"})
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_collapse_preserves_multiply_driven_intermediate_net(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="not", inputs=["a"], output="mid"))
        design.add_gate(Gate(name="U_multi", type="buf", inputs=["b"], output="mid"))
        design.add_gate(Gate(name="U1", type="not", inputs=["mid"], output="y"))

        result = collapse_back_to_back_inverters(design)

        self.assertEqual(result["num_changed"], 0)
        self.assertEqual(set(design.gates), {"U0", "U1", "U_multi"})

    def test_replace_or_with_nand_not_rewrites_only_target_cone(self) -> None:
        design = Design(inputs={"a", "b", "c"}, outputs={"flag", "other"})
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="flag"))
        design.add_gate(Gate(name="U_other_or", type="or", inputs=["b", "c"], output="other"))

        result = replace_or_with_nand_not(design, "flag")

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_or"].type, "nand")
        self.assertEqual(len(design.gates["U_or"].inputs), 2)
        added_gates = result["changed"][0]["added_gates"]
        self.assertEqual([design.gates[name].type for name in added_gates], ["not", "not"])
        self.assertEqual(design.gates["U_other_or"].type, "or")

    def test_insert_buffers_for_fanout_builds_equivalent_buffer_tree(self) -> None:
        design = Design(inputs={"src"}, outputs={f"y{i}" for i in range(5)})
        for i in range(5):
            design.add_gate(Gate(name=f"U{i}", type="buf", inputs=["src"], output=f"y{i}"))
        before = deepcopy(design)

        result = insert_buffers_for_fanout(design, "src", 2)

        self.assertGreater(result["num_inserted_buffers"], 0)
        self.assertTrue(check_fanout(design, 2)["ok"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])
        self.assertLessEqual(len(design.fanouts["src"]), 2)

    def test_insert_buffers_for_fanout_rejects_impossible_bound(self) -> None:
        design = Design(inputs={"src"}, outputs={"src", "y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y"))
        rebuild_graph(design)

        with self.assertRaisesRegex(ValueError, "max_fanout >= 2"):
            insert_buffers_for_fanout(design, "src", 1)

    def test_insert_dedicated_buffers_for_each_load(self) -> None:
        design = Design(inputs={"src"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="y1"))
        before = deepcopy(design)

        result = insert_dedicated_buffers_for_each_load(design, "src")

        self.assertEqual(result["num_inserted_buffers"], 2)
        self.assertEqual(len(design.fanouts["src"]), 2)
        self.assertTrue(all(sink.startswith("GATE:") for sink in design.fanouts["src"]))
        self.assertNotEqual(design.gates["U0"].inputs, ["src"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_balance_depth_with_buffers_equalizes_endpoint_depths(self) -> None:
        design = Design(inputs={"src"}, outputs={"y0", "y1", "y2"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y1"))
        design.add_gate(Gate(name="U3", type="buf", inputs=["src"], output="n2"))
        design.add_gate(Gate(name="U4", type="buf", inputs=["n2"], output="n3"))
        design.add_gate(Gate(name="U5", type="buf", inputs=["n3"], output="y2"))
        before = deepcopy(design)

        result = balance_depth_with_buffers(design, "src", ["y0", "y1", "y2"])

        self.assertEqual(result["num_inserted_buffers"], 3)
        self.assertEqual({max_depth(design, "src", dst)[0] for dst in ["y0", "y1", "y2"]}, {3})
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_balance_depth_rejects_multiply_driven_destination(self) -> None:
        design = Design(inputs={"src", "other"}, outputs={"y0", "y1"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["src"], output="y0"))
        design.add_gate(Gate(name="U_multi", type="buf", inputs=["other"], output="y0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["src"], output="n1"))
        design.add_gate(Gate(name="U2", type="buf", inputs=["n1"], output="y1"))

        with self.assertRaisesRegex(ValueError, "not uniquely driven"):
            balance_depth_with_buffers(design, "src", ["y0", "y1"])

    def test_optimize_cone_removes_buffer_and_double_inverter(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["a"], output="n_buf"))
        design.add_gate(Gate(name="U_not0", type="not", inputs=["n_buf"], output="n_inv"))
        design.add_gate(Gate(name="U_not1", type="not", inputs=["n_inv"], output="n_clean"))
        design.add_gate(Gate(name="U_and", type="and", inputs=["n_clean", "b"], output="y"))
        before = deepcopy(design)

        result = optimize_cone(design, "y", max_depth=1)

        self.assertEqual(result["final_gate_count"], 1)
        self.assertEqual(result["final_depth"], 1)
        self.assertEqual(set(design.gates), {"U_and"})
        self.assertEqual(design.gates["U_and"].inputs, ["a", "b"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])


    def test_optimize_cone_uses_cone_local_yosys_abc_result(self) -> None:
        design = Design(inputs={"a", "b", "c", "d", "e", "f", "g", "h", "i"}, outputs={"y"})
        previous = "a"
        for index, input_net in enumerate(["b", "c", "d", "e", "f", "g", "h", "i"]):
            output = "y" if index == 7 else f"n{index}"
            design.add_gate(Gate(name=f"U{index}", type="and", inputs=[previous, input_net], output=output))
            previous = output
        rebuild_graph(design)

        def fake_run_yosys_script(script: str, cwd: Path | None = None, timeout: float | None = None):
            if "write_verilog" in script and cwd is not None:
                out_path = Path(cwd) / "cone_optimized.v"
                out_path.write_text(
                    "module cone_opt(a, b, c, d, e, f, g, h, i, y);\n"
                    "input a, b, c, d, e, f, g, h, i;\n"
                    "output y;\n"
                    "and U_opt(y, a, b, c, d, e, f, g, h, i);\n"
                    "endmodule\n",
                    encoding="utf-8",
                )
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("eda.transform.run_yosys_script", side_effect=fake_run_yosys_script), patch(
            "parser.verilog_writer.run_yosys_script", side_effect=fake_run_yosys_script
        ):
            result = optimize_cone(design, "y")

        self.assertEqual(result["engine"], "local_plus_cone_local_yosys_abc")
        self.assertEqual(result["final_depth"], 1)
        self.assertEqual(result["final_gate_count"], 1)
        self.assertEqual(len(design.gates), 1)
        self.assertEqual(next(iter(design.gates.values())).output, "y")

    def test_cone_local_yosys_abc_gate_limit_is_3000(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        optimized = deepcopy(design)

        with patch("eda.transform._optimize_cone_with_yosys_abc", return_value=(optimized, {"engine": "fake"})) as yosys_abc:
            result = _try_yosys_abc_for_small_cone(
                design,
                target="y",
                resolved_target="y",
                max_depth=None,
                initial_gate_count=3000,
                initial_depth=2,
                current_gate_count=3000,
                current_depth=2,
            )

        yosys_abc.assert_called_once()
        self.assertIsNotNone(result)

        with patch("eda.transform._optimize_cone_with_yosys_abc") as yosys_abc:
            result = _try_yosys_abc_for_small_cone(
                design,
                target="y",
                resolved_target="y",
                max_depth=None,
                initial_gate_count=3001,
                initial_depth=2,
                current_gate_count=3001,
                current_depth=2,
            )

        yosys_abc.assert_not_called()
        self.assertIsNone(result)

    def test_optimize_design_depth_uses_yosys_abc_result(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="and", inputs=["n0", "b"], output="y"))
        rebuild_graph(design)

        optimized = Design(inputs={"a", "b"}, outputs={"y"})
        optimized.add_gate(Gate(name="U_opt", type="and", inputs=["a", "b"], output="y"))
        rebuild_graph(optimized)

        def fake_write_verilog(_design: Design, path: str | Path) -> None:
            Path(path).write_text(
                "module top(a, b, y);\n"
                "input a, b;\n"
                "output y;\n"
                "wire n0;\n"
                "buf U0(n0, a);\n"
                "and U1(y, n0, b);\n"
                "endmodule\n",
                encoding="utf-8",
            )

        def fake_run_yosys_script(_script: str, cwd: Path | None = None, timeout: float | None = None):
            out_path = Path(cwd or tempfile.gettempdir()) / "optimized.v"
            out_path.write_text(
                "module top(a, b, y); input a, b; output y; and U_opt(y, a, b); endmodule\n",
                encoding="utf-8",
            )
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("eda.transform.write_verilog", side_effect=fake_write_verilog), patch(
            "eda.transform.run_yosys_script", side_effect=fake_run_yosys_script
        ), patch("eda.transform.parse_verilog", return_value=optimized):
            result = optimize_design_depth(design, max_depth=1)

        self.assertEqual(result["engine"], "yosys_abc")
        self.assertEqual(result["initial_depth"], 2)
        self.assertEqual(result["final_depth"], 1)
        self.assertTrue(result["target_met"])
        self.assertEqual(set(design.gates), {"U_opt"})

    def test_adaptive_depth_topk_thresholds(self) -> None:
        self.assertEqual(_adaptive_depth_topk(2000, False), 32)
        self.assertEqual(_adaptive_depth_topk(8000, False), 32)
        self.assertEqual(_adaptive_depth_topk(20000, False), 16)
        self.assertEqual(_adaptive_depth_topk(20001, False), 8)
        self.assertEqual(_adaptive_depth_topk(2000, True), 8)
        self.assertEqual(_adaptive_depth_topk(8000, True), 8)
        self.assertEqual(_adaptive_depth_topk(20000, True), 8)
        self.assertEqual(_adaptive_depth_topk(20001, True), 8)

    def test_abc_candidate_depth_targets_use_75_percent_and_plain_abc(self) -> None:
        self.assertEqual(_abc_candidate_depth_targets(35), [("target_27", 27), ("plain_abc", None)])

    def test_canonicalize_yosys_generated_names_uses_contest_style_names(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"}, wires={"_0001_", "_yosys_bit_7", "g12.q"})
        design.add_gate(
            Gate(
                name="U_$and$_tmp_tmpabcd_input_for_yosys_v_10$3",
                type="and",
                inputs=["a", "_yosys_bit_7"],
                output="_0001_",
            )
        )
        design.add_gate(
            Gate(
                name="U_$not$_tmp_tmpabcd_input_for_yosys_v_11$4",
                type="not",
                inputs=["_0001_"],
                output="y",
            )
        )
        design.add_dff(DFF(name="g12", q="g12.q", d="_yosys_bit_7", clk="clk"))
        rebuild_graph(design)

        _canonicalize_yosys_generated_names(design)

        self.assertEqual(set(design.gates), {"g13", "g14"})
        self.assertNotIn("g12.q", design.wires)
        self.assertNotIn("_0001_", design.wires)
        self.assertNotIn("_yosys_bit_7", design.wires)
        self.assertTrue(all("input_for_yosys" not in name for name in design.gates))
        self.assertTrue(all("." not in net and "tmp" not in net and "yosys" not in net for net in design.wires))
        self.assertIn(design.dffs["g12"].q, design.wires)
        self.assertIn(design.dffs["g12"].d, design.wires)

    def test_optimize_design_depth_falls_back_when_yosys_fails(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n0"], output="y"))
        rebuild_graph(design)

        with patch("eda.transform.write_verilog", side_effect=RuntimeError("Yosys missing")):
            result = optimize_design_depth(design)

        self.assertEqual(result["engine"], "topk_after_yosys_fallback")
        self.assertIn("Yosys missing", result["fallback_reason"])
        self.assertEqual(result["final_depth"], 1)
        self.assertEqual(set(design.gates), {"U1"})
        self.assertEqual(design.gates["U1"].inputs, ["a"])

    def test_optimize_design_depth_falls_back_to_adaptive_topk_for_fanout_buffered_design(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n__fanout_buf_0"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["n__fanout_buf_0"], output="y"))
        rebuild_graph(design)

        with patch("eda.transform._optimize_design_depth_with_yosys_abc", side_effect=RuntimeError("Yosys timeout")) as yosys_abc:
            result = optimize_design_depth(design)

        yosys_abc.assert_called_once()
        self.assertEqual(result["engine"], "topk_after_yosys_fallback")
        self.assertEqual(result["max_outputs"], 8)
        self.assertIn("Yosys/ABC failed", result["bounded_reason"])
        self.assertIn("Yosys timeout", result["fallback_reason"])

    def test_optimize_design_depth_topk_fallback_enables_cone_yosys_abc(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        rebuild_graph(design)

        def fake_optimize_cone(_design, _output, **kwargs):
            return {"num_changed": 0, "allow_yosys_abc": kwargs["allow_yosys_abc"]}

        with patch("eda.transform._optimize_design_depth_with_yosys_abc", side_effect=RuntimeError("Yosys timeout")), patch(
            "eda.transform.optimize_cone", side_effect=fake_optimize_cone
        ) as cone_optimizer:
            result = optimize_design_depth(design)

        self.assertEqual(result["engine"], "topk_after_yosys_fallback")
        cone_optimizer.assert_called_once()
        self.assertTrue(cone_optimizer.call_args.kwargs["allow_yosys_abc"])
        self.assertIn("attempted top", result["bounded_reason"])
        self.assertIn("cone-local Yosys/ABC", result["bounded_reason"])

    def test_optimize_design_depth_falls_back_to_adaptive_topk_for_large_design(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        previous = "a"
        for index in range(10001):
            out = "y" if index == 10000 else f"n{index}"
            design.add_gate(Gate(name=f"U{index}", type="buf", inputs=[previous], output=out))
            previous = out
        rebuild_graph(design)

        with patch("eda.transform._optimize_design_depth_with_yosys_abc", side_effect=AssertionError("too expensive")) as yosys_abc:
            result = optimize_design_depth(design)

        yosys_abc.assert_not_called()
        self.assertEqual(result["engine"], "adaptive_topk_critical_cones")
        self.assertEqual(result["max_outputs"], 16)
        self.assertEqual(result["initial_depth"], 10001)
        self.assertEqual(result["final_depth"], 10001)
        self.assertIn("skipped full-design Yosys/ABC", result["bounded_reason"])

    def test_optimize_design_depth_large_topk_attempts_cone_yosys_before_cleanup(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="Y0", type="buf", inputs=["a"], output="mid"))
        design.add_gate(Gate(name="Y1", type="buf", inputs=["mid"], output="y"))
        previous = "b"
        for index in range(10001):
            out = f"dummy{index}"
            design.add_gate(Gate(name=f"DUMMY{index}", type="buf", inputs=[previous], output=out))
            previous = out
        rebuild_graph(design)

        def fake_optimize_cone(_design, target, **kwargs):
            return {
                "target": target,
                "engine": "local_plus_cone_local_yosys_abc",
                "num_changed": 1,
                "changed": [{"rule": "cone_local_yosys_abc"}],
                "allow_yosys_abc": kwargs["allow_yosys_abc"],
            }

        def fake_cleanup(_design, max_depth=None):
            return {
                "engine": "large_design_bounded_cleanup",
                "max_depth": max_depth,
                "max_outputs": 0,
                "attempted_outputs": [],
                "skipped_outputs": [],
                "initial_gate_count": len(_design.gates),
                "final_gate_count": len(_design.gates),
                "initial_depth": None,
                "final_depth": None,
                "target_met": max_depth is None,
                "changed": [{"op": "remove_dangling", "num_removed_gates": 1}],
                "num_changed_outputs": 1,
                "bounded_reason": "cleanup",
            }

        with patch("eda.transform._optimize_design_depth_with_yosys_abc", side_effect=RuntimeError("Yosys timeout")), patch(
            "eda.transform.optimize_cone", side_effect=fake_optimize_cone
        ) as cone_optimizer, patch("eda.transform._optimize_design_depth_fast_cleanup", side_effect=fake_cleanup) as cleanup:
            result = optimize_design_depth(design)

        cone_optimizer.assert_called_once()
        self.assertEqual(cone_optimizer.call_args.args[1], "y")
        self.assertTrue(cone_optimizer.call_args.kwargs["allow_yosys_abc"])
        cleanup.assert_called_once()
        self.assertEqual(result["engine"], "adaptive_topk_critical_cones")
        self.assertEqual(result["attempted_outputs"], ["y"])
        self.assertEqual(result["initial_depth"], 2)
        self.assertEqual(result["final_depth"], 2)
        self.assertEqual(result["num_changed_outputs"], 2)
        self.assertEqual(result["changed"][0]["engine"], "local_plus_cone_local_yosys_abc")
        self.assertEqual(result["changed"][1]["op"], "remove_dangling")

    def test_optimize_cone_resolves_dff_q_to_d_input_cone(self) -> None:
        design = Design(inputs={"a"}, outputs={"q"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="mid"))
        design.add_gate(Gate(name="U1", type="buf", inputs=["mid"], output="d"))
        design.dffs["FF0"] = DFF(name="FF0", q="q", d="d", clk="clk", rst=None)
        rebuild_graph(design)

        result = optimize_cone(design, "q")

        self.assertEqual(result["resolved_target"], "d")
        self.assertEqual(result["target_resolution"]["kind"], "dff_q_to_d")
        self.assertEqual(result["final_gate_count"], 0)
        self.assertEqual(design.dffs["FF0"].d, "a")

    def test_optimize_design_depth_accepts_cone_gate_constraint(self) -> None:
        design = Design(inputs={"a", "b", "c"}, outputs={"n10", "y"})
        design.add_gate(Gate(name="U_xor", type="xor", inputs=["a", "b"], output="n10"))
        design.add_gate(Gate(name="U_buf", type="buf", inputs=["c"], output="y"))
        rebuild_graph(design)

        def fake_depth_optimizer(candidate: Design, max_depth=None, max_outputs=None):
            return {
                "engine": "fake_depth",
                "attempted_outputs": ["n10", "y"],
                "skipped_outputs": [],
                "changed": [],
                "num_changed_outputs": 0,
                "bounded_reason": None,
            }

        with patch("eda.transform._optimize_design_depth_base", side_effect=fake_depth_optimizer):
            result = optimize_design_depth(
                design,
                cost_function="max_logic_depth",
                cost_scope="whole_design",
                constraints=[
                    {"type": "cone_gate_library", "target": "n10", "allowed_gates": ["nor", "not"]}
                ],
            )

        self.assertEqual(result["engine"], "constraint_aware_depth")
        self.assertTrue(result["candidate_applied"])
        self.assertTrue(result["constraint_reports_after"][0]["satisfied"])
        self.assertEqual(result["constraint_reports_after"][0]["allowed_gates"], ["nor", "not"])
        self.assertTrue(all(gate.type in {"nor", "not"} for gate in design.gates.values() if gate.output == "n10" or gate.output.startswith("U_xor_")))

    def test_optimize_design_depth_preserves_whole_design_allowed_gates(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y"))
        original = deepcopy(design)

        def fake_depth_optimizer(candidate: Design, max_depth=None, max_outputs=None):
            return {
                "engine": "fake_depth",
                "attempted_outputs": ["y"],
                "skipped_outputs": [],
                "changed": [],
                "num_changed_outputs": 0,
                "bounded_reason": None,
            }

        with patch("eda.transform._optimize_design_depth_base", side_effect=fake_depth_optimizer):
            result = optimize_design_depth(design, allowed_gates=["and", "not"])

        self.assertEqual(result["engine"], "library_preserving_depth")
        self.assertTrue(result["gate_library_report_after"]["satisfied"])
        self.assertEqual(result["gate_library_report_after"]["disallowed_gate_counts"], {})
        self.assertTrue(all(gate.type in {"and", "not"} for gate in design.gates.values()))
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_optimize_design_depth_bounds_large_allowed_gate_pass(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        for index in range(10001):
            design.add_gate(Gate(name=f"U{index}", type="and", inputs=["a", "b"], output=f"n{index}"))
        design.outputs = {"n10000"}
        seen_max_outputs: list[int | None] = []

        def fake_depth_optimizer(candidate: Design, max_depth=None, max_outputs=None):
            seen_max_outputs.append(max_outputs)
            return {
                "engine": "fake_depth",
                "attempted_outputs": [],
                "skipped_outputs": [],
                "changed": [],
                "num_changed_outputs": 0,
                "bounded_reason": "fake",
            }

        with (
            patch("eda.transform._design_max_depth", return_value=1),
            patch("eda.transform._optimize_design_depth_base", side_effect=fake_depth_optimizer),
        ):
            optimize_design_depth(design, allowed_gates=["and", "not"])

        self.assertEqual(seen_max_outputs, [16])

    def test_optimize_design_depth_skips_full_yosys_for_large_design(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        for index in range(10001):
            design.add_gate(Gate(name=f"U{index}", type="and", inputs=["a", "b"], output=f"n{index}"))
        design.outputs = {"n10000"}

        with (
            patch("eda.transform._design_max_depth", return_value=1),
            patch("eda.transform._optimize_design_depth_with_yosys_abc", side_effect=AssertionError("too expensive")),
        ):
            result = optimize_design_depth(design)

        self.assertEqual(result["engine"], "adaptive_topk_critical_cones")
        self.assertIn("skipped full-design Yosys/ABC", result["bounded_reason"])

    def test_optimize_cone_accepts_nand_not_allowed_gates(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "b"], output="y"))
        original = deepcopy(design)

        result = optimize_cone(design, "y", allowed_gates=["nand", "not"], allow_yosys_abc=False)

        self.assertEqual(result["engine"], "constraint_aware_cone")
        self.assertEqual(result["allowed_gates"], ["nand", "not"])
        self.assertTrue(all(design.gates[name].type in {"nand", "not"} for name in logic_cone(design, "y")))
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_constrained_optimize_cone_resolves_dff_q_to_d_input_cone(self) -> None:
        design = Design(inputs={"a", "clk"}, outputs={"q"})
        design.add_gate(Gate(name="U_or", type="or", inputs=["a", "clk"], output="d"))
        design.add_dff(DFF(name="FF0", d="d", q="q", clk="clk"))
        original = deepcopy(design)

        result = optimize_cone(design, "q", allowed_gates=["nand", "not"], allow_yosys_abc=False)

        self.assertEqual(result["target_resolution"]["kind"], "dff_q_to_d")
        self.assertEqual(result["resolved_target"], "d")
        self.assertGreater(result["initial_gate_count"], 0)
        self.assertTrue(all(design.gates[name].type in {"nand", "not"} for name in logic_cone(design, "d")))
        self.assertTrue(check_design_equivalence(original, design)["ok"])

    def test_rename_net_updates_references_and_preserves_function(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="n_mid"))
        design.add_gate(Gate(name="U1", type="not", inputs=["n_mid"], output="y"))
        before = deepcopy(design)

        result = rename_net(design, "n_mid", "renamed_mid")

        self.assertEqual(result["old_net"], "n_mid")
        self.assertEqual(result["new_net"], "renamed_mid")
        self.assertNotIn("n_mid", design.wires)
        self.assertIn("renamed_mid", design.wires)
        self.assertEqual(design.gates["U0"].output, "renamed_mid")
        self.assertEqual(design.gates["U1"].inputs, ["renamed_mid"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_rename_net_rejects_existing_net_collision(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="and", inputs=["a", "b"], output="y"))

        with self.assertRaisesRegex(ValueError, "existing net"):
            rename_net(design, "a", "b")

    def test_constant_propagation_simplifies_constants_and_preserves_function(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "1'b1"], output="n_mid"))
        design.add_gate(Gate(name="U_or", type="or", inputs=["n_mid", "1'b0"], output="y"))
        before = deepcopy(design)

        result = constant_propagation(design)

        self.assertGreaterEqual(result["num_changed"], 1)
        self.assertNotIn("U_and", design.gates)
        self.assertNotIn("n_mid", design.wires)
        self.assertEqual(design.gates["U_or"].type, "buf")
        self.assertEqual(design.gates["U_or"].inputs, ["a"])
        self.assertTrue(check_design_equivalence(before, design)["ok"])

    def test_constant_propagation_rewrites_constant_primary_output_driver(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U_and", type="and", inputs=["a", "1'b0"], output="y"))

        result = constant_propagation(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_and"].type, "buf")
        self.assertEqual(design.gates["U_and"].inputs, ["1'b0"])

    def test_constant_propagation_preserves_multiply_driven_output(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y"})
        design.add_gate(Gate(name="U_const", type="and", inputs=["a", "1'b0"], output="shared"))
        design.add_gate(Gate(name="U_multi", type="buf", inputs=["b"], output="shared"))
        design.add_gate(Gate(name="U_out", type="buf", inputs=["shared"], output="y"))

        result = constant_propagation(design)

        self.assertEqual(result["num_changed"], 0)
        self.assertEqual(design.gates["U_const"].type, "and")
        self.assertIn("U_multi", design.gates)

    def test_replace_nand_const1_with_not_rewrites_specific_identity(self) -> None:
        design = Design(inputs={"a", "b"}, outputs={"y", "z"})
        design.add_gate(Gate(name="U_nand1", type="nand", inputs=["a", "1'b1"], output="y"))
        design.add_gate(Gate(name="U_nand0", type="nand", inputs=["b", "1'b0"], output="z"))

        result = replace_nand_const1_with_not(design)

        self.assertEqual(result["num_changed"], 1)
        self.assertEqual(design.gates["U_nand1"].type, "not")
        self.assertEqual(design.gates["U_nand1"].inputs, ["a"])
        self.assertEqual(design.gates["U_nand0"].type, "nand")


if __name__ == "__main__":
    unittest.main()
