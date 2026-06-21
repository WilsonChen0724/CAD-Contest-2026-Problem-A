from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_release_outputs import _validate_ledger


class ReleaseValidatorTest(unittest.TestCase):
    def test_validates_gate_count_record_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test01"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            snapshot_path = snapshot_dir / "response_003_after.v"
            snapshot_path.write_text(
                "\n".join(
                    [
                        "module top(a, b, y);",
                        "input a, b;",
                        "output y;",
                        "wire n1;",
                        "and U1(n1, a, b);",
                        "not U2(y, n1);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            ledger_path = case_dir / "ledger.jsonl"
            record = {
                "case": "test01",
                "response_id": 3,
                "prompt": "Report gate counts.",
                "plan": {"steps": [{"op": "report_gate_counts", "args": {}}]},
                "body": "\n".join(
                    [
                        "Gate counts:",
                        "- and: 1",
                        "- or: 0",
                        "- not: 1",
                        "- nand: 0",
                        "- nor: 0",
                        "- xor: 0",
                        "- xnor: 0",
                        "- buf: 0",
                        "- dff: 0",
                        "Total gates: 2",
                    ]
                ),
                "after_snapshot": "snapshots/response_003_after.v",
            }
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "test01")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "PASS")
            self.assertEqual(results[0].check, "report_gate_counts")

    def test_validates_path_depth_and_cone_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test02"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            snapshot_path = snapshot_dir / "design.v"
            snapshot_path.write_text(
                "\n".join(
                    [
                        "module top(a, b, y, z);",
                        "input a, b;",
                        "output y, z;",
                        "wire n1, n2;",
                        "and U1(n1, a, b);",
                        "not U2(n2, n1);",
                        "buf U3(y, n2);",
                        "or U4(z, a, b);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            records = [
                {
                    "case": "test02",
                    "response_id": 1,
                    "plan": {"op": "find_path", "args": {"src": "a", "dst": "y"}},
                    "body": "Found path:\na -> U1 -> n1 -> U2 -> n2 -> U3 -> y",
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 2,
                    "plan": {"op": "max_depth", "args": {"src": "a", "dst": "y"}},
                    "body": (
                        'The maximum logic depth from "a" to "y" is 3.\n'
                        "Example path: a -> U1 -> n1 -> U2 -> n2 -> U3 -> y"
                    ),
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 3,
                    "plan": {"op": "logic_cone", "args": {"target": "y"}},
                    "body": 'Logic cone of "y" contains 3 gates:\nU1\nU2\nU3',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 4,
                    "plan": {"op": "report_fanout_cone", "args": {"source": "a"}},
                    "body": (
                        'Transitive fanout cone of "a": '
                        "4 gate(s), 5 net(s), 2 primary output(s), 0 DFF sink(s)."
                    ),
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 5,
                    "plan": {
                        "op": "all_paths_pass_through",
                        "args": {"src": "a", "dst": "y", "node": "n1"},
                    },
                    "body": 'Yes. Every combinational path from "a" to "y" passes through "n1".',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 6,
                    "plan": {"op": "report_all_paths", "args": {"src": "a", "dst": "y"}},
                    "body": 'Combinational paths from "a" to "y": 1\n1. a -> U1 -> n1 -> U2 -> n2 -> U3 -> y',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 7,
                    "plan": {"op": "report_io_counts", "args": {}},
                    "body": "Primary IO counts:\n- inputs: 2\n- outputs: 2",
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 8,
                    "plan": {"op": "report_primary_inputs", "args": {}},
                    "body": "Primary inputs: 2\n- a: 1 bit(s)\n- b: 1 bit(s)",
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 9,
                    "plan": {"op": "report_gates_by_type", "args": {"gate_type": "and"}},
                    "body": "AND gates: 1\n- U1: inputs [a, b], output n1",
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 10,
                    "plan": {"op": "report_gate_connections", "args": {"gate": "U1"}},
                    "body": 'Gate "U1": type=and, inputs=[a, b], output=n1.\nOutput fanout:\n- GATE:U2',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 11,
                    "plan": {"op": "report_gate_type_count_in_cone", "args": {"target": "y", "gate_type": "not"}},
                    "body": 'NOT gates in the fanin cone of "y": 1 out of 3 cone gate(s).',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 12,
                    "plan": {"op": "report_max_logic_depth", "args": {}},
                    "body": (
                        "The maximum combinational logic depth in the design is 3.\n"
                        "Example endpoint: y.\n"
                        "Example path: a -> U1 -> n1 -> U2 -> n2 -> U3 -> y"
                    ),
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "test02",
                    "response_id": 13,
                    "plan": {"op": "check_fanout", "args": {"max_fanout": 2}},
                    "body": "{'ok': True, 'violations': {}}",
                    "after_snapshot": "snapshots/design.v",
                },
            ]
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            results = _validate_ledger(release_dir, ledger_path, "test02")

            self.assertEqual([result.status for result in results], ["PASS"] * len(records))

    def test_negative_records_fail_when_response_text_is_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad01"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            snapshot_path = snapshot_dir / "design.v"
            snapshot_path.write_text(
                "\n".join(
                    [
                        "module top(a, b, y);",
                        "input a, b;",
                        "output y;",
                        "wire n1;",
                        "and U1(n1, a, b);",
                        "not U2(y, n1);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            records = [
                {
                    "case": "bad01",
                    "response_id": 1,
                    "plan": {"op": "report_gate_counts", "args": {}},
                    "body": "Gate counts:\n- and: 99\nTotal gates: 99",
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "bad01",
                    "response_id": 2,
                    "plan": {"op": "find_path", "args": {"src": "a", "dst": "y"}},
                    "body": 'No path found from "a" to "y".',
                    "after_snapshot": "snapshots/design.v",
                },
                {
                    "case": "bad01",
                    "response_id": 3,
                    "plan": {"op": "max_depth", "args": {"src": "a", "dst": "y"}},
                    "body": 'The maximum logic depth from "a" to "y" is 1.\nExample path: a -> y',
                    "after_snapshot": "snapshots/design.v",
                },
            ]
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            results = _validate_ledger(release_dir, ledger_path, "bad01")

            self.assertEqual([result.status for result in results], ["FAIL", "FAIL", "FAIL"])

    def test_negative_transform_record_fails_when_snapshots_are_not_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad02"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            before_path = snapshot_dir / "before.v"
            after_path = snapshot_dir / "after.v"
            before_path.write_text(
                "\n".join(
                    [
                        "module top(a, y);",
                        "input a;",
                        "output y;",
                        "buf U1(y, a);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            after_path.write_text(
                "\n".join(
                    [
                        "module top(a, y);",
                        "input a;",
                        "output y;",
                        "not U1(y, a);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            record = {
                "case": "bad02",
                "response_id": 1,
                "plan": {"op": "remove_dangling", "args": {}},
                "body": "Removed dangling logic: 0 gate(s), 0 wire(s).",
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "bad02")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "FAIL")
            self.assertEqual(results[0].check, "remove_dangling")

    def test_missing_transform_snapshots_are_inconclusive_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad03"
            case_dir.mkdir(parents=True)
            record = {
                "case": "bad03",
                "response_id": 1,
                "plan": {"op": "remove_dangling", "args": {}},
                "body": "Removed dangling logic: 0 gate(s), 0 wire(s).",
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "bad03")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "INCONCLUSIVE")

    def test_validates_last_transform_input_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test03"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            before_path = snapshot_dir / "last_transform_input.v"
            after_path = snapshot_dir / "after.v"
            netlist = "\n".join(
                [
                    "module top(a, y);",
                    "input a;",
                    "output y;",
                    "buf U1(y, a);",
                    "endmodule",
                ]
            )
            before_path.write_text(netlist, encoding="utf-8")
            after_path.write_text(netlist, encoding="utf-8")
            records = [
                {
                    "case": "test03",
                    "response_id": 1,
                    "plan": {"op": "check_equivalent_to_last_transform_input", "args": {}},
                    "body": "Equivalent to the pre-transformation netlist. Checked with z3 engine over combinational boundaries.",
                    "after_snapshot": "snapshots/after.v",
                    "last_transform_input_snapshot": "snapshots/last_transform_input.v",
                    "last_transform": {"transform": "remove_dangling", "result": {}},
                },
                {
                    "case": "test03",
                    "response_id": 2,
                    "plan": {"op": "check_equivalent_to_last_transform_input", "args": {}},
                    "body": "No previous successful transform input snapshot is available. Run a transform first.",
                },
            ]
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            results = _validate_ledger(release_dir, ledger_path, "test03")

            self.assertEqual([result.status for result in results], ["PASS", "PASS"])

    def test_negative_last_transform_input_equivalence_fails_on_wrong_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad04"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            before_path = snapshot_dir / "last_transform_input.v"
            after_path = snapshot_dir / "after.v"
            before_path.write_text(
                "\n".join(
                    [
                        "module top(a, y);",
                        "input a;",
                        "output y;",
                        "buf U1(y, a);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            after_path.write_text(
                "\n".join(
                    [
                        "module top(a, y);",
                        "input a;",
                        "output y;",
                        "not U1(y, a);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            record = {
                "case": "bad04",
                "response_id": 1,
                "plan": {"op": "check_equivalent_to_last_transform_input", "args": {}},
                "body": "Equivalent to the pre-transformation netlist. Checked with z3 engine over combinational boundaries.",
                "after_snapshot": "snapshots/after.v",
                "last_transform_input_snapshot": "snapshots/last_transform_input.v",
                "last_transform": {"transform": "remove_dangling", "result": {}},
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "bad04")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "FAIL")


if __name__ == "__main__":
    unittest.main()
