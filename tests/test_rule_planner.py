from __future__ import annotations

import unittest

from agent.planner import plan_request


class RulePlannerTest(unittest.TestCase):
    def test_maps_release_style_read_design_path(self) -> None:
        plan = plan_request(
            "Please load the design from the file test01.v located in the directory testcase/test01/.",
            None,
        )

        self.assertEqual(plan, {"op": "read_design", "args": {"path": "testcase/test01/test01.v"}})

    def test_maps_all_paths_pass_through(self) -> None:
        plan = plan_request("Does every path from A to B pass through C?", None)

        self.assertEqual(plan, {"op": "all_paths_pass_through", "args": {"src": "A", "dst": "B", "node": "C"}})

    def test_maps_report_all_paths(self) -> None:
        plan = plan_request("List all paths from A to B.", None)

        self.assertEqual(plan, {"op": "report_all_paths", "args": {"src": "A", "dst": "B"}})

    def test_maps_complete_path_enumeration_between_two_signals(self) -> None:
        plan = plan_request("Provide a complete enumeration of paths between n0[1] and n63[0].", None)

        self.assertEqual(plan, {"op": "report_all_paths", "args": {"src": "n0[1]", "dst": "n63[0]"}})

    def test_maps_primary_output_cone_size_report(self) -> None:
        plan = plan_request("Report all primary outputs whose logic cone contains more than 100 gates.", None)

        self.assertEqual(plan, {"op": "report_outputs_by_cone_size", "args": {"min_gates": 100}})

    def test_maps_same_clock_domain(self) -> None:
        plan = plan_request("Does FF0 and FF1 under the same clock domain?", None)

        self.assertEqual(plan, {"op": "same_clock_domain", "args": {"dff_a": "FF0", "dff_b": "FF1"}})

    def test_maps_structural_reports(self) -> None:
        self.assertEqual(
            plan_request("Report the total number of gates broken down by type.", None),
            {"op": "report_gate_counts", "args": {}},
        )
        self.assertEqual(
            plan_request(
                "Please count all the gates in this design and report the total count broken down by gate type (AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, DFF).",
                None,
            ),
            {"op": "report_gate_counts", "args": {}},
        )
        self.assertEqual(
            plan_request("How many NOT gates are in the current design?", None),
            {"op": "report_gate_type_count", "args": {"gate_type": "not"}},
        )
        self.assertEqual(
            plan_request("Report the fanout of net n1.", None),
            {"op": "report_fanout", "args": {"net": "n1"}},
        )
        self.assertEqual(
            plan_request("Report the gate type and pin connections of gate U0.", None),
            {"op": "report_gate_connections", "args": {"gate": "U0"}},
        )
        self.assertEqual(
            plan_request("List all primary outputs of this design with their bit widths.", None),
            {"op": "report_primary_outputs", "args": {}},
        )
        self.assertEqual(
            plan_request("Determine the number of primary inputs and outputs.", None),
            {"op": "report_io_counts", "args": {}},
        )
        self.assertEqual(
            plan_request("What is the transitive fanout of primary input n0? List all gates reachable from n0.", None),
            {"op": "report_fanout_cone", "args": {"source": "n0"}},
        )
        self.assertEqual(
            plan_request("Report any NAND gates with constant inputs (0 or 1) in this design.", None),
            {"op": "report_constant_input_gates", "args": {"gate_type": "nand"}},
        )
        self.assertEqual(
            plan_request("Report all gates shared between the fanin cones of n16 and n17.", None),
            {"op": "report_shared_fanin_cone_gates", "args": {"target_a": "n16", "target_b": "n17"}},
        )
        self.assertEqual(
            plan_request("Report every gate connected to the output of g0.", None),
            {"op": "report_fanout", "args": {"net": "g0"}},
        )
        self.assertEqual(
            plan_request("List all gates that now connect to the renamed signal renamed_sig.", None),
            {"op": "report_fanout", "args": {"net": "renamed_sig"}},
        )
        self.assertEqual(
            plan_request("What is the depth of the cone of n14 now?", None),
            {"op": "report_cone_depth", "args": {"target": "n14"}},
        )
        self.assertEqual(
            plan_request("Which output has the largest fanin cone?", None),
            {"op": "report_largest_fanin_cone_output", "args": {}},
        )

    def test_maps_dff_enable_hold_reports_before_generic_gate_rules(self) -> None:
        self.assertEqual(
            plan_request(
                "Report the D input logic of the flip-flops to report any existing enable or hold structures implemented through multiplexers or AND gates.",
                None,
            ),
            {"op": "report_dff_input_logic_structures", "args": {}},
        )
        self.assertEqual(
            plan_request(
                "How many flip-flops were found to have enable or hold structures in their D input logic?",
                None,
            ),
            {"op": "report_dff_input_logic_structures", "args": {}},
        )

    def test_maps_gate_on_max_depth_path(self) -> None:
        plan = plan_request("Determine whether gate g0 lies on any maximum-depth path of the design.", None)

        self.assertEqual(plan, {"op": "gate_on_max_depth_path", "args": {"gate": "g0"}})

    def test_maps_register_to_register_paths(self) -> None:
        plan = plan_request("List all register-to-register paths in this design through combinational logic.", None)

        self.assertEqual(plan, {"op": "report_register_paths", "args": {}})

    def test_maps_register_to_register_depth(self) -> None:
        plan = plan_request("What is the maximum combinational depth on any register-to-register path in this design?", None)

        self.assertEqual(plan, {"op": "report_max_register_to_register_depth", "args": {}})

    def test_maps_remove_dangling(self) -> None:
        plan = plan_request("Remove any dangling gates and nets.", None)

        self.assertEqual(plan, {"op": "remove_dangling", "args": {}})

    def test_maps_eliminate_unused_logic(self) -> None:
        plan = plan_request("Eliminate unused logic gates from the netlist. Ensure functional equivalence is preserved.", None)

        self.assertEqual(plan, {"op": "remove_dangling", "args": {}})

    def test_maps_remove_floating_nodes(self) -> None:
        plan = plan_request("Remove floating nodes that do not affect outputs. Make sure nothing changes functionally.", None)

        self.assertEqual(plan, {"op": "remove_dangling", "args": {}})

    def test_maps_replace_inv_buf_with_inv(self) -> None:
        plan = plan_request("Replace all inverters followed by buffers with a single inverter.", None)

        self.assertEqual(plan, {"op": "replace_inv_buf_with_inv", "args": {}})

    def test_maps_collapse_back_to_back_inverters(self) -> None:
        plan = plan_request("Find all back-to-back inverter pairs and collapse them into a wire.", None)

        self.assertEqual(plan, {"op": "collapse_back_to_back_inverters", "args": {}})

    def test_maps_replace_or_with_nand_not(self) -> None:
        plan = plan_request("Replace all 2-input OR gates in the cone of flag with NAND and NOT gates.", None)

        self.assertEqual(plan, {"op": "replace_or_with_nand_not", "args": {"cone_target": "flag"}})

    def test_maps_insert_buffers_for_fanout(self) -> None:
        plan = plan_request("Insert buffers on high-fanout net clk_en so fanout is at most 8.", None)

        self.assertEqual(plan, {"op": "insert_buffers_for_fanout", "args": {"net": "clk_en", "max_fanout": 8}})

    def test_maps_insert_buffers_for_all_high_fanout(self) -> None:
        plan = plan_request("Insert buffers wherever needed across the design so fanout is at most 8.", None)

        self.assertEqual(plan, {"op": "insert_buffers_for_all_high_fanout", "args": {"max_fanout": 8}})

        plan = plan_request("Perform fanout optimization across the netlist with maximum fanout 4. Ensure functional equivalence is preserved.", None)

        self.assertEqual(plan, {"op": "insert_buffers_for_all_high_fanout", "args": {"max_fanout": 4}})

        plan = plan_request(
            "Insert buffers wherever needed so that no signal drives more than 16 loads. The cost function is the total gate count of the final design; smaller is better.",
            None,
        )

        self.assertEqual(plan, {"op": "insert_buffers_for_all_high_fanout", "args": {"max_fanout": 16}})

    def test_maps_insert_dedicated_buffers_for_each_load(self) -> None:
        plan = plan_request(
            "Please insert a BUF gate on signal n2 so that each load of n2 is driven through a dedicated buffer.",
            None,
        )

        self.assertEqual(plan, {"op": "insert_dedicated_buffers_for_each_load", "args": {"net": "n2"}})

    def test_maps_balance_depth_with_buffers(self) -> None:
        plan = plan_request("Balance depths from source src to destinations y0, y1 with buffers.", None)

        self.assertEqual(
            plan,
            {
                "op": "balance_depth_with_buffers",
                "args": {"src": "src", "dsts": ["y0", "y1"], "minimize_buffers": True},
            },
        )

    def test_maps_optimize_cone(self) -> None:
        plan = plan_request(
            "Optimize the logic cone of h so that the maximum depth is less than or equal to 5 and the gate count is minimized.",
            None,
        )

        self.assertEqual(
            plan,
            {"op": "optimize_cone", "args": {"target": "h", "minimize_gate_count": True, "max_depth": 5}},
        )

    def test_maps_rename_net(self) -> None:
        plan = plan_request("Rename internal signal n_mid to renamed_mid.", None)

        self.assertEqual(plan, {"op": "rename_net", "args": {"old_net": "n_mid", "new_net": "renamed_mid"}})

    def test_maps_rename_gate_before_rename_net(self) -> None:
        plan = plan_request("Rename gate g123 to renamed_g123.", None)

        self.assertEqual(plan, {"op": "rename_gate", "args": {"old_name": "g123", "new_name": "renamed_g123"}})

    def test_maps_release_identifier_rename_wording(self) -> None:
        self.assertEqual(
            plan_request("Change the identifier of gate g0 to renamed_gate and update all references.", None),
            {"op": "rename_gate", "args": {"old_name": "g0", "new_name": "renamed_gate"}},
        )
        self.assertEqual(
            plan_request("Change the identifier of wire n74 to renamed_wire and update all references.", None),
            {"op": "rename_net", "args": {"old_net": "n74", "new_net": "renamed_wire"}},
        )
        self.assertEqual(
            plan_request("Update the name of signal n7431 to renamed_wire throughout the netlist.", None),
            {"op": "rename_net", "args": {"old_net": "n7431", "new_net": "renamed_wire"}},
        )

    def test_maps_design_level_transformations(self) -> None:
        self.assertEqual(
            plan_request("Optimize design depth so the maximum depth is at most 12.", None),
            {
                "op": "optimize_design_depth",
                "args": {
                    "cost_function": "max_logic_depth",
                    "objective": "minimize",
                    "cost_scope": "whole_design",
                    "max_depth": 12,
                },
            },
        )
        self.assertEqual(
            plan_request("Perform depth optimization on the combinational logic. Ensure functional equivalence is preserved.", None),
            {
                "op": "optimize_design_depth",
                "args": {
                    "cost_function": "max_logic_depth",
                    "objective": "minimize",
                    "cost_scope": "whole_design",
                },
            },
        )
        self.assertEqual(
            plan_request("Replace XNOR and NOR gates with equivalent basic gates.", None),
            {"op": "replace_xnor_nor_with_basic_gates", "args": {}},
        )
        self.assertEqual(
            plan_request("Rewrite all XNOR gates using only NOR and NOT gates. Ensure the design functionality does not change.", None),
            {"op": "replace_xnor_with_nor", "args": {}},
        )
        self.assertEqual(
            plan_request("Report the total NOR gate count after replacing all XNOR gates.", None),
            {"op": "report_gate_type_count", "args": {"gate_type": "nor"}},
        )
        self.assertEqual(
            plan_request("Remap AND and NOT gates into NAND-only logic.", None),
            {"op": "replace_and_not_with_nand", "args": {}},
        )
        self.assertEqual(
            plan_request("Reconstruct the entire netlist using only AND and NOT gates while preserving functional equivalence.", None),
            {"op": "replace_with_and_not", "args": {}},
        )
        self.assertEqual(
            plan_request("Merge equivalent duplicate gates.", None),
            {"op": "merge_equivalent_gates", "args": {}},
        )

    def test_maps_constant_propagation(self) -> None:
        plan = plan_request("Propagate constants and simplify gates with tied constant inputs.", None)

        self.assertEqual(plan, {"op": "constant_propagation", "args": {}})

    def test_maps_nand_const1_rewrite(self) -> None:
        plan = plan_request(
            "Try to replace all 2-input NAND gates that have one input tied to constant 1 with inverters.",
            None,
        )

        self.assertEqual(plan, {"op": "replace_nand_const1_with_not", "args": {}})

    def test_maps_original_equivalence_check(self) -> None:
        plan = plan_request("Verify the current design is equivalent to the original loaded netlist.", None)

        self.assertEqual(plan, {"op": "check_equivalent_to_original", "args": {}})

    def test_maps_pre_transform_equivalence_check(self) -> None:
        plan = plan_request("Prove that the transformed design is equivalent to the pre-transformation netlist.", None)

        self.assertEqual(plan, {"op": "check_equivalent_to_last_transform_input", "args": {}})

    def test_maps_equivalence_check(self) -> None:
        plan = plan_request("Check whether (a & b) is equivalent to z.", None)

        self.assertEqual(plan, {"op": "check_equivalence", "args": {"expr": "(a & b)", "target": "z"}})

    def test_maps_release_constant_and_signal_equivalence_wording(self) -> None:
        self.assertEqual(
            plan_request("Is output n16 always 0 regardless of all inputs? Report yes or no.", None),
            {"op": "check_equivalence", "args": {"expr": "0", "target": "n16"}},
        )
        self.assertEqual(
            plan_request("Check functional equivalence between internal signals n1287 and n2404.", None),
            {"op": "check_equivalence", "args": {"expr": "n1287", "target": "n2404"}},
        )
        self.assertEqual(
            plan_request("Determine whether signals n13082 and n13083 are functionally equivalent.", None),
            {"op": "check_equivalence", "args": {"expr": "n13082", "target": "n13083"}},
        )
        self.assertEqual(
            plan_request("Verify that n2257 and n2184 produce identical logic values for all inputs.", None),
            {"op": "check_equivalence", "args": {"expr": "n2257", "target": "n2184"}},
        )
        self.assertEqual(
            plan_request(
                "Check whether internal signals n29498 and n29471 are functionally equivalent for all input combinations.",
                None,
            ),
            {"op": "check_equivalence", "args": {"expr": "n29498", "target": "n29471"}},
        )

    def test_maps_release_test31_backend_prompts(self) -> None:
        self.assertEqual(
            plan_request("Derive the Boolean equation for output n16 in terms of its primary inputs.", None),
            {"op": "derive_boolean_equation", "args": {"target": "n16"}},
        )
        self.assertEqual(
            plan_request("How many BUF gates were added by the buffer insertion just performed?", None),
            {"op": "report_last_transform_stats", "args": {}},
        )
        self.assertEqual(
            plan_request("What is the maximum logic depth from any primary input to any DFF D-pin in this design?", None),
            {"op": "report_max_depth_to_dff_d", "args": {}},
        )
        self.assertEqual(
            plan_request("What is the maximum combinational depth from any primary input to any primary output in the entire design?", None),
            {"op": "report_max_logic_depth", "args": {}},
        )
        self.assertEqual(
            plan_request("How many outputs have a logic depth greater than 4?", None),
            {"op": "report_outputs_depth_greater_than", "args": {"min_depth": 4}},
        )

    def test_maps_late_release_backend_prompts(self) -> None:
        self.assertEqual(
            plan_request("List all NAND gates in this design with their input and output signals.", None),
            {"op": "report_gate_type_connections", "args": {"gate_type": "nand"}},
        )
        self.assertEqual(
            plan_request("Report any AND gates with a constant 0 input in this design.", None),
            {"op": "report_constant_input_gates", "args": {"gate_type": "and"}},
        )
        self.assertEqual(
            plan_request("What Boolean function does output n25 compute? Express it in terms of the primary inputs.", None),
            {"op": "derive_boolean_equation", "args": {"target": "n25"}},
        )
        self.assertEqual(
            plan_request("Write the logic expression for n30 using only the primary input names.", None),
            {"op": "derive_boolean_equation", "args": {"target": "n30"}},
        )
        self.assertEqual(
            plan_request("How many NAND gates are now in the restructured cone of output n8?", None),
            {"op": "report_gate_type_count_in_cone", "args": {"target": "n8", "gate_type": "nand"}},
        )
        self.assertEqual(
            plan_request("Try to restructure n10 with a target depth of 4, preserving functionality. Report original if already optimal.", None),
            {"op": "optimize_cone", "args": {"target": "n10", "minimize_gate_count": True, "max_depth": 4}},
        )
        self.assertEqual(
            plan_request("Reduce the critical path depth through restructuring. Make sure nothing changes functionally.", None),
            {
                "op": "optimize_design_depth",
                "args": {
                    "cost_function": "max_logic_depth",
                    "objective": "minimize",
                    "cost_scope": "whole_design",
                },
            },
        )
        self.assertEqual(
            plan_request(
                "Minimize the maximum logic depth of the design, ensuring the netlist remains AND and NOT only.",
                None,
            ),
            {
                "op": "optimize_design_depth",
                "args": {
                    "cost_function": "max_logic_depth",
                    "objective": "minimize",
                    "cost_scope": "whole_design",
                    "allowed_gates": ["and", "not"],
                },
            },
        )
        self.assertEqual(
            plan_request("Calculate the critical path depth between n15 and n25[0].", None),
            {"op": "max_depth", "args": {"src": "n15", "dst": "n25[0]"}},
        )
        self.assertEqual(
            plan_request("Determine the number of gates driven by g0.", None),
            {"op": "report_fanout", "args": {"net": "g0"}},
        )
        self.assertEqual(
            plan_request("Enumerate the immediate successors of gate g0.", None),
            {"op": "report_fanout", "args": {"net": "g0"}},
        )
        self.assertEqual(
            plan_request("How many floating signals were found?", None),
            {"op": "check_connectivity", "args": {}},
        )
        self.assertEqual(
            plan_request("Convert every XOR gate in this design to an equivalent 4-NAND circuit. Ensure the design functionality does not change.", None),
            {"op": "replace_xor_with_nand", "args": {}},
        )
        self.assertEqual(
            plan_request("Are there any redundant gates in this design that can be removed without changing functionality? Remove them if found.", None),
            {"op": "merge_equivalent_gates", "args": {}},
        )
        self.assertEqual(
            plan_request("Confirm that the design is still functionally equivalent to the original.", None),
            {"op": "check_equivalent_to_original", "args": {}},
        )
        self.assertEqual(
            plan_request(
                "Does there exist any pair of internal signals (a, b) already in the netlist such that NAND(a, b) is equivalent to n25?",
                None,
            ),
            {"op": "find_nand_equivalent_pair", "args": {"target": "n25"}},
        )

    def test_maps_asserted_only_when_property(self) -> None:
        plan = plan_request("For output done, verify that it is asserted only when both req is 1 and busy is 0.", None)

        self.assertEqual(
            plan,
            {"op": "check_property", "args": {"target": "done", "property": "done -> (req & !busy)"}},
        )


if __name__ == "__main__":
    unittest.main()
