from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eda.design import Design, Gate
from runtime.state import CurrentState
from runtime.validation_ledger import append_validation_record, snapshot_design, snapshot_last_transform_input


class ValidationLedgerTest(unittest.TestCase):
    def test_snapshot_and_record_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            validation_dir = Path(tmp) / "validation"
            design = Design(module_name="top", inputs={"a"}, outputs={"y"})
            design.add_gate(Gate(name="U1", type="buf", inputs=["a"], output="y"))
            state = CurrentState(validation_enabled=True, validation_dir=validation_dir)
            state.begin_testcase("test01")
            state.design = design

            snapshot = snapshot_design(state, 2, "after")
            state.last_transform_input = design
            last_transform_input_snapshot = snapshot_last_transform_input(state, 2)
            append_validation_record(
                state,
                response_id=2,
                prompt="Report gate counts.",
                plan={"steps": [{"op": "report_gate_counts", "args": {}}]},
                body="Gate counts:\n- buf: 1\nTotal gates: 1",
                before_snapshot=None,
                after_snapshot=snapshot,
                last_transform_input_snapshot=last_transform_input_snapshot,
            )

            self.assertIsNotNone(snapshot)
            self.assertTrue(Path(snapshot).exists())
            self.assertIsNotNone(last_transform_input_snapshot)
            self.assertTrue(Path(last_transform_input_snapshot).exists())
            ledger_lines = state.validation_ledger_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_lines), 1)
            record = json.loads(ledger_lines[0])
            self.assertEqual(record["case"], "test01")
            self.assertEqual(record["response_id"], 2)
            self.assertEqual(record["plan"]["steps"][0]["op"], "report_gate_counts")
            self.assertEqual(record["last_transform_input_snapshot"], last_transform_input_snapshot)


if __name__ == "__main__":
    unittest.main()
