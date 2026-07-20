from __future__ import annotations

import unittest

from agent.intent_classifier import (
    CONSTRAINT_PRESERVING_TRANSFORM,
    DESIGN_IO,
    EXPLICIT_COST_OPTIMIZATION,
    READ_ONLY_COST_ANALYSIS,
    READ_ONLY_VERIFICATION,
    UNSUPPORTED_OR_AMBIGUOUS,
    classify_plan,
    classify_prompt,
    validate_semantic_plan_match,
)
from agent.plan_checker import PlanValidationError


class IntentClassifierTest(unittest.TestCase):
    def test_classifies_prompt_categories(self) -> None:
        self.assertEqual(
            classify_prompt("Please load the design from testcase/test31/test31.v."),
            DESIGN_IO,
        )
        self.assertEqual(
            classify_prompt("How many gates are in the logic cone of output n12?"),
            READ_ONLY_COST_ANALYSIS,
        )
        self.assertEqual(
            classify_prompt("The cost function is the maximum logic depth of the final design; smaller is better."),
            EXPLICIT_COST_OPTIMIZATION,
        )
        self.assertEqual(
            classify_prompt("Replace every XOR gate with an equivalent NAND-only implementation."),
            CONSTRAINT_PRESERVING_TRANSFORM,
        )
        self.assertEqual(
            classify_prompt("Verify functional equivalence between the current design and the original loaded netlist."),
            READ_ONLY_VERIFICATION,
        )
        self.assertEqual(classify_prompt("Please help with this design."), UNSUPPORTED_OR_AMBIGUOUS)

    def test_classifies_plan_categories(self) -> None:
        self.assertEqual(classify_plan({"op": "read_design", "args": {"path": "x.v"}}), DESIGN_IO)
        self.assertEqual(classify_plan({"op": "report_gate_counts", "args": {}}), READ_ONLY_COST_ANALYSIS)
        self.assertEqual(classify_plan({"op": "check_equivalent_to_original", "args": {}}), READ_ONLY_VERIFICATION)
        self.assertEqual(classify_plan({"op": "replace_xor_with_nand", "args": {}}), CONSTRAINT_PRESERVING_TRANSFORM)
        self.assertEqual(
            classify_plan({"op": "optimize_design_depth", "args": {}}, "Minimize maximum logic depth."),
            EXPLICIT_COST_OPTIMIZATION,
        )
        self.assertEqual(
            classify_plan({"op": "unsupported", "args": {"reason": "ambiguous"}}),
            UNSUPPORTED_OR_AMBIGUOUS,
        )

    def test_rejects_original_vs_previous_transform_equivalence_mismatch(self) -> None:
        with self.assertRaisesRegex(PlanValidationError, "original loaded snapshot"):
            validate_semantic_plan_match(
                "Check whether the current netlist is equivalent to the netlist as last loaded from disk.",
                {"op": "check_equivalent_to_last_transform_input", "args": {}},
            )
        with self.assertRaisesRegex(PlanValidationError, "previous transform input"):
            validate_semantic_plan_match(
                "Prove the current design is equivalent to the pre-transformation netlist.",
                {"op": "check_equivalent_to_original", "args": {}},
            )


if __name__ == "__main__":
    unittest.main()
