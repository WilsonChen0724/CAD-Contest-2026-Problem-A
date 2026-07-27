from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eda.design import DFF, Design, Gate
from scripts import validate_release_outputs as validator
from scripts.validate_release_outputs import (
    ValidationResult,
    _collect_metrics_for_cases,
    _validate_large_transform_with_guards,
    _validate_ledger,
)


class ReleaseValidatorTest(unittest.TestCase):
    def test_all_paths_validator_uses_exact_count_above_enumeration_bound(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate("U0", "buf", ["src"], "n0"))
        design.add_gate(Gate("U1", "not", ["n0"], "n1"))
        design.add_gate(Gate("U2", "buf", ["n0"], "n2"))
        design.add_gate(Gate("U3", "or", ["n1", "n2"], "dst"))

        with patch.object(validator, "_require_snapshot", return_value=design):
            result = validator._validate_all_paths(
                Path("release"),
                Path("ledger.jsonl"),
                "test00",
                1,
                {"src": "src", "dst": "dst", "max_paths": 1},
                'Combinational paths from "src" to "dst": 1\nEnumeration was truncated after 1 path(s).',
                {},
            )

        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertIn("1 of 2 exact path(s)", result.detail)

    def test_all_paths_validator_checks_complete_report_above_inline_bound(self) -> None:
        design = Design(module_name="top", inputs={"src"}, outputs={"dst"})
        design.add_gate(Gate("U0", "buf", ["src"], "n0"))
        design.add_gate(Gate("U1", "not", ["n0"], "n1"))
        design.add_gate(Gate("U2", "buf", ["n0"], "n2"))
        design.add_gate(Gate("U3", "or", ["n1", "n2"], "dst"))

        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp)
            report_path = release_dir / "output" / "reports" / "paths.txt"
            report_path.parent.mkdir(parents=True)
            result = validator.all_paths(design, "src", "dst", max_paths=3)
            report_lines = ['Combinational paths from "src" to "dst": 2']
            report_lines.extend(
                f"{index}. " + " -> ".join(path)
                for index, path in enumerate(result["paths"], 1)
            )
            report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
            body = (
                'Combinational paths from "src" to "dst": 2\n'
                "Full path listing written to output\\reports\\paths.txt.\n"
                "Showing first 2 path(s) in this response."
            )
            with (
                patch.object(validator, "_require_snapshot", return_value=design),
                patch.object(validator, "DEFAULT_COMPLETE_PATH_LIMIT", 1),
            ):
                verdict = validator._validate_all_paths(
                    release_dir,
                    Path("ledger.jsonl"),
                    "test00",
                    1,
                    {"src": "src", "dst": "dst"},
                    body,
                    {},
                )

        self.assertEqual(verdict.status, "PASS")
        self.assertIn("all 2 exact path(s)", verdict.detail)

    def test_fanout_validator_handles_floating_signal_placeholder(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"y"}, wires={"floating_wire"})
        design.add_gate(Gate("U0", "buf", ["a"], "y"))
        body = "\n".join(
            [
                "Floating/unconnected signal report: 1 issue(s) found.",
                "- missing drivers: 1",
                "- duplicate drivers: 0",
                "Signals with missing drivers:",
                "- floating_wire",
            ]
        )

        with patch.object(validator, "_require_snapshot", return_value=design):
            result = validator._validate_fanout(
                Path("release"),
                Path("ledger.jsonl"),
                "test00",
                1,
                {"net": "floating_signals"},
                body,
                {},
            )

        self.assertEqual(result.status, "PASS")
        self.assertIn("connectivity oracle", result.detail)

    def test_find_gates_accepts_none_wording_for_zero_matches(self) -> None:
        design = Design(module_name="top", inputs={"a"}, outputs={"y"})
        design.add_gate(Gate("U0", "buf", ["a"], "y"))

        with patch.object(validator, "_require_snapshot", return_value=design):
            result = validator._validate_find_gates(
                Path("release"),
                Path("ledger.jsonl"),
                "test00",
                1,
                {"gate_type": "xor"},
                "Matched gates: none.",
                {},
            )

        self.assertEqual(result.status, "PASS")

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
                "before_snapshot": "snapshots/response_003_after.v",
                "after_snapshot": "snapshots/response_003_after.v",
            }
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "test01")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "PASS")
            self.assertEqual(results[0].check, "report_gate_counts")
            self.assertEqual(
                results[0].ledger_sha256,
                hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                results[0].before_snapshot_sha256,
                hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                results[0].after_snapshot_sha256,
                hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                results[0].validator_sha256,
                hashlib.sha256(Path(validator.__file__).read_bytes()).hexdigest(),
            )

    def test_snapshot_parse_error_marks_record_inconclusive_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test_parse_blocked"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "after.v").write_text("module top(a, y); input a; output y; buf U1(y, a); endmodule")
            ledger_path = case_dir / "ledger.jsonl"
            record = {
                "case": "test_parse_blocked",
                "response_id": 1,
                "plan": {"op": "report_gate_counts", "args": {}},
                "body": "Gate counts:\n- buf: 1\nTotal gates: 1",
                "after_snapshot": "snapshots/after.v",
            }
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with patch.object(validator, "parse_verilog", side_effect=OSError("Yosys blocked")):
                results = _validate_ledger(release_dir, ledger_path, "test_parse_blocked")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "INCONCLUSIVE")
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

    def test_large_analysis_graph_oracles_are_exact_not_inconclusive(self) -> None:
        original_limit = validator.VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT
        validator.VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT = 1
        try:
            with tempfile.TemporaryDirectory() as tmp:
                release_dir = Path(tmp) / "release"
                case_dir = release_dir / "runner_output" / "rule" / "validation" / "test_large_analysis"
                snapshot_dir = case_dir / "snapshots"
                snapshot_dir.mkdir(parents=True)
                snapshot_path = snapshot_dir / "design.v"
                snapshot_path.write_text(
                    "\n".join(
                        [
                            "module top(a, b, y);",
                            "input a, b;",
                            "output y;",
                            "wire n1, n2;",
                            "and U1(n1, a, b);",
                            "not U2(n2, n1);",
                            "buf U3(y, n2);",
                            "endmodule",
                        ]
                    ),
                    encoding="utf-8",
                )
                records = [
                    {
                        "case": "test_large_analysis",
                        "response_id": 1,
                        "plan": {"op": "find_path", "args": {"src": "a", "dst": "y"}},
                        "body": "Found path:\na -> U1 -> n1 -> U2 -> n2 -> U3 -> y",
                        "after_snapshot": "snapshots/design.v",
                    },
                    {
                        "case": "test_large_analysis",
                        "response_id": 2,
                        "plan": {
                            "op": "all_paths_pass_through",
                            "args": {"src": "a", "dst": "y", "node": "n1"},
                        },
                        "body": 'Yes. Every combinational path from "a" to "y" passes through "n1".',
                        "after_snapshot": "snapshots/design.v",
                    },
                    {
                        "case": "test_large_analysis",
                        "response_id": 3,
                        "plan": {"op": "max_depth", "args": {"src": "a", "dst": "y"}},
                        "body": (
                            'The maximum logic depth from "a" to "y" is 3.\n'
                            "Example path: a -> U1 -> n1 -> U2 -> n2 -> U3 -> y"
                        ),
                        "after_snapshot": "snapshots/design.v",
                    },
                    {
                        "case": "test_large_analysis",
                        "response_id": 4,
                        "plan": {"op": "report_max_logic_depth", "args": {}},
                        "body": "The maximum combinational logic depth in the design is 3.",
                        "after_snapshot": "snapshots/design.v",
                    },
                ]
                ledger_path = case_dir / "ledger.jsonl"
                ledger_path.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )

                results = _validate_ledger(release_dir, ledger_path, "test_large_analysis")

                self.assertEqual([result.status for result in results], ["PASS"] * len(records))
        finally:
            validator.VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT = original_limit

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

    def test_remove_dangling_uses_wire_only_source_certificate_before_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test_wire"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            before_path = snapshot_dir / "before.v"
            after_path = snapshot_dir / "after.v"
            before_path.write_text(
                "module top(a, y);\ninput a;\noutput y;\nwire unused, y;\nbuf U0(y, a);\nendmodule\n",
                encoding="utf-8",
            )
            after_path.write_text(
                "module top(a, y);\ninput a;\noutput y;\nwire y;\nbuf U0(y, a);\nendmodule\n",
                encoding="utf-8",
            )
            record = {
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
            }

            with patch.object(validator, "_parse_snapshot", side_effect=AssertionError("parser should not run")):
                result = validator._validate_transform(
                    release_dir,
                    case_dir / "ledger.jsonl",
                    "test_wire",
                    1,
                    "remove_dangling",
                    {},
                    "Removed dangling logic: 0 gate(s), 1 net(s).",
                    record,
                )

            self.assertEqual(result.status, "PASS")
            self.assertIn("only 1 wire declaration(s) were removed", result.detail)

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

    def test_collects_transform_qor_metrics_from_ledger_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "llm_openai" / "validation" / "test26"
            case_dir.mkdir(parents=True)
            ledger_path = case_dir / "ledger.jsonl"
            record = {
                "case": "test26",
                "response_id": 7,
                "plan": {"op": "optimize_design_depth", "args": {}},
                "body": "Optimized design depth: gates 23 -> 15, depth 9 -> 6.",
                "last_transform": {
                    "delta": {
                        "before_total_gates": 23,
                        "after_total_gates": 15,
                        "total_gate_delta": -8,
                    },
                    "result": {
                        "initial_depth": 9,
                        "final_depth": 6,
                        "num_changed_outputs": 2,
                    },
                },
            }
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            metrics = _collect_metrics_for_cases(
                release_dir,
                {"test26": ledger_path},
                [
                    ValidationResult(
                        "test26",
                        7,
                        "PASS",
                        "optimize_design_depth",
                        "equivalence passed",
                    )
                ],
            )

            self.assertEqual(len(metrics), 1)
            metric = metrics[0]
            self.assertEqual(metric.before_gates, 23)
            self.assertEqual(metric.after_gates, 15)
            self.assertEqual(metric.gate_delta, -8)
            self.assertTrue(metric.gate_improved)
            self.assertEqual(metric.before_depth, 9)
            self.assertEqual(metric.after_depth, 6)
            self.assertEqual(metric.depth_delta, -3)
            self.assertTrue(metric.depth_improved)
            self.assertEqual(metric.validation_status, "PASS")
            self.assertEqual(metric.cost_objective, "max_logic_depth")

    def test_bounded_noop_transform_passes_from_ledger_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "llm_openai" / "validation" / "test24"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            netlist = "\n".join(
                [
                    "module top(a, y);",
                    "input a;",
                    "output y;",
                    "buf U1(y, a);",
                    "endmodule",
                ]
            )
            (snapshot_dir / "before.v").write_text(netlist, encoding="utf-8")
            (snapshot_dir / "after.v").write_text(netlist, encoding="utf-8")
            record = {
                "case": "test24",
                "response_id": 6,
                "plan": {"op": "optimize_design_depth", "args": {}},
                "body": (
                    "Optimized design depth with adaptive_topk_critical_cones: "
                    "gates 1 -> 1, depth 1 -> 1, changed targets 0. "
                    "large design: skipped full-design Yosys/ABC due to budget."
                ),
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
                "last_transform": {
                    "transform": "optimize_design_depth",
                    "delta": {
                        "before_total_gates": 1,
                        "after_total_gates": 1,
                        "total_gate_delta": 0,
                        "type_delta": {},
                        "added_gates": [],
                        "removed_gates": [],
                        "added_dffs": [],
                        "removed_dffs": [],
                        "added_nets": [],
                        "removed_nets": [],
                    },
                    "result": {"num_changed_outputs": 0, "changed": []},
                },
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "test24")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "PASS")
            self.assertIn("no structural delta", results[0].detail)

    def test_delta_zero_with_reported_change_still_checks_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad05"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "before.v").write_text(
                "\n".join(
                    [
                        "module top(a, b, y);",
                        "input a, b;",
                        "output y;",
                        "and U1(y, a, b);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            (snapshot_dir / "after.v").write_text(
                "\n".join(
                    [
                        "module top(a, b, y);",
                        "input a, b;",
                        "output y;",
                        "or U1(y, a, b);",
                        "endmodule",
                    ]
                ),
                encoding="utf-8",
            )
            record = {
                "case": "bad05",
                "response_id": 1,
                "plan": {"op": "reconnect_gate_input", "args": {"gate": "U1", "pin": "A", "new_net": "b"}},
                "body": "Gate U1 was reconnected successfully.",
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
                "last_transform": {
                    "transform": "reconnect_gate_input",
                    "delta": {
                        "before_total_gates": 1,
                        "after_total_gates": 1,
                        "total_gate_delta": 0,
                        "type_delta": {},
                        "added_gates": [],
                        "removed_gates": [],
                        "added_dffs": [],
                        "removed_dffs": [],
                        "added_nets": [],
                        "removed_nets": [],
                    },
                    "result": {"num_changed": 1, "changed": [{"gate": "U1"}]},
                },
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            results = _validate_ledger(release_dir, ledger_path, "bad05")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "FAIL")
            self.assertIn("not equivalent", results[0].detail)

    def test_large_collapse_passes_connectivity_and_residual_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "test_collapse"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "before.v").write_text(
                "module top(a, y); input a; output y; wire n; not U0(n, a); not U1(y, n); endmodule",
                encoding="utf-8",
            )
            (snapshot_dir / "after.v").write_text(
                "module top(a, y); input a; output y; buf U1(y, a); endmodule",
                encoding="utf-8",
            )
            record = {
                "case": "test_collapse",
                "response_id": 1,
                "plan": {"op": "collapse_back_to_back_inverters", "args": {}},
                "body": "Collapsed 1 back-to-back inverter pair(s).",
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with patch.object(validator, "VALIDATOR_FULL_TRANSFORM_EQ_GATE_LIMIT", 0):
                results = _validate_ledger(release_dir, ledger_path, "test_collapse")

            self.assertEqual(results[0].status, "PASS")
            self.assertIn("connectivity regression passed", results[0].detail)
            self.assertIn("no collapsible", results[0].detail)

    def test_large_collapse_skip_fails_when_pairs_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "bad_collapse"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            netlist = "module top(a, y); input a; output y; wire n; not U0(n, a); not U1(y, n); endmodule"
            (snapshot_dir / "before.v").write_text(netlist, encoding="utf-8")
            (snapshot_dir / "after.v").write_text(netlist, encoding="utf-8")
            record = {
                "case": "bad_collapse",
                "response_id": 1,
                "plan": {"op": "collapse_back_to_back_inverters", "args": {}},
                "body": "Skipped back-to-back inverter collapse for this large design.",
                "before_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
            }
            ledger_path = case_dir / "ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with patch.object(validator, "VALIDATOR_FULL_TRANSFORM_EQ_GATE_LIMIT", 0):
                results = _validate_ledger(release_dir, ledger_path, "bad_collapse")

            self.assertEqual(results[0].status, "FAIL")
            self.assertIn("inverter pair(s) remain", results[0].detail)

    def test_large_linear_analysis_oracles_do_not_skip_by_gate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "large_analysis"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "design.v").write_text(
                (
                    "module top(a, b, y); input a, b; output y; wire n; "
                    "buf U0(n, a); and U1(y, n, b); endmodule"
                ),
                encoding="utf-8",
            )
            ledger_path = case_dir / "ledger.jsonl"
            record = {"after_snapshot": "snapshots/design.v"}

            with patch.object(validator, "VALIDATOR_EXPENSIVE_ANALYSIS_GATE_LIMIT", 0):
                direct = validator._validate_direct_pi_po_paths(
                    release_dir,
                    ledger_path,
                    "large_analysis",
                    1,
                    "Direct PI-to-PO zero-gate paths: 0",
                    record,
                )
                paths = validator._validate_all_paths(
                    release_dir,
                    ledger_path,
                    "large_analysis",
                    2,
                    {"src": "y", "dst": "a"},
                    'Combinational paths from "y" to "a": 0',
                    record,
                )
                cut = validator._validate_cut_signal(
                    release_dir,
                    ledger_path,
                    "large_analysis",
                    3,
                    {"signal": "n"},
                    'Yes. "n" is a cut between primary input "a" and primary output "y".',
                    record,
                )

            self.assertEqual([direct.status, paths.status, cut.status], ["PASS", "PASS", "PASS"])

    def test_large_compositional_transform_proofs(self) -> None:
        rename_before = Design(module_name="top", inputs={"a"}, outputs={"y"})
        rename_before.add_gate(Gate("U0", "buf", ["a"], "old_net"))
        rename_before.add_gate(Gate("U1", "not", ["old_net"], "y"))
        rename_after = Design(module_name="top", inputs={"a"}, outputs={"y"})
        rename_after.add_gate(Gate("U0", "buf", ["a"], "new_net"))
        rename_after.add_gate(Gate("U1", "not", ["new_net"], "y"))

        gate_rename_before = Design(module_name="top", inputs={"a"}, outputs={"y"})
        gate_rename_before.add_gate(Gate("old_gate", "buf", ["a"], "y"))
        gate_rename_after = Design(module_name="top", inputs={"a"}, outputs={"y"})
        gate_rename_after.add_gate(Gate("new_gate", "buf", ["a"], "y"))

        dangling_before = Design(module_name="top", inputs={"a"}, outputs={"y"}, wires={"unused"})
        dangling_before.add_gate(Gate("U0", "buf", ["a"], "y", attrs={"src": "before.v:1"}))
        dangling_after = Design(module_name="top", inputs={"a"}, outputs={"y"})
        dangling_after.add_gate(Gate("U0", "buf", ["a"], "y", attrs={"src": "after.v:1"}))

        buffer_before = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1"})
        buffer_before.add_gate(Gate("U0", "not", ["src"], "y0"))
        buffer_before.add_gate(Gate("U1", "buf", ["src"], "y1"))
        buffer_after = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1"})
        buffer_after.add_gate(Gate("U0", "not", ["src_buf0"], "y0"))
        buffer_after.add_gate(Gate("U1", "buf", ["src_buf1"], "y1"))
        buffer_after.add_gate(Gate("B0", "buf", ["src"], "src_buf0"))
        buffer_after.add_gate(Gate("B1", "buf", ["src"], "src_buf1"))

        rename = validator._check_large_compositional_transform(
            rename_before,
            rename_after,
            "rename_net",
            {"old_net": "old_net", "new_net": "new_net"},
        )
        gate_rename = validator._check_large_compositional_transform(
            gate_rename_before,
            gate_rename_after,
            "rename_gate",
            {"old_name": "old_gate", "new_name": "new_gate"},
        )
        dangling = validator._check_large_compositional_transform(
            dangling_before,
            dangling_after,
            "remove_dangling",
            {},
        )
        buffers = validator._check_large_compositional_transform(
            buffer_before,
            buffer_after,
            "insert_dedicated_buffers_for_each_load",
            {"net": "src"},
        )

        self.assertEqual(rename[0], "PASS")
        self.assertEqual(gate_rename[0], "PASS")
        self.assertEqual(dangling[0], "PASS")
        self.assertEqual(buffers[0], "PASS")

    def test_large_buffer_tree_compositional_proof(self) -> None:
        before = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1", "y2", "y3"})
        for index in range(4):
            before.add_gate(Gate(f"U{index}", "not", ["src"], f"y{index}"))
        after = Design(module_name="top", inputs={"src"}, outputs={"y0", "y1", "y2", "y3"})
        after.add_gate(Gate("B0", "buf", ["src"], "b0"))
        after.add_gate(Gate("B1", "buf", ["src"], "b1"))
        for index in range(4):
            after.add_gate(Gate(f"U{index}", "not", ["b0" if index < 2 else "b1"], f"y{index}"))

        result = validator._check_large_compositional_transform(
            before,
            after,
            "insert_buffers_for_fanout",
            {"net": "src", "max_fanout": 2},
        )

        self.assertEqual(result[0], "PASS")
        self.assertIn("fanout <= 2", result[1])

    def test_large_buffer_forest_compositional_proof(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1", "y2", "y3"})
        before.add_gate(Gate("U0", "not", ["a"], "y0"))
        before.add_gate(Gate("U1", "not", ["a"], "y1"))
        before.add_gate(Gate("U2", "not", ["b"], "y2"))
        before.add_gate(Gate("U3", "not", ["b"], "y3"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y0", "y1", "y2", "y3"})
        after.add_gate(Gate("BA0", "buf", ["a"], "a_buf"))
        after.add_gate(Gate("BB0", "buf", ["b"], "b_buf"))
        after.add_gate(Gate("U0", "not", ["a_buf"], "y0"))
        after.add_gate(Gate("U1", "not", ["a_buf"], "y1"))
        after.add_gate(Gate("U2", "not", ["b_buf"], "y2"))
        after.add_gate(Gate("U3", "not", ["b_buf"], "y3"))

        result = validator._check_large_compositional_transform(
            before,
            after,
            "insert_buffers_for_all_high_fanout",
            {"max_fanout": 2},
        )

        self.assertEqual(result[0], "PASS")
        self.assertIn("2 root net(s)", result[1])
        self.assertIn("full-design fanout <= 2", result[1])

    def test_large_cone_validation_selects_dff_input_and_checks_gate_library(self) -> None:
        before = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"q"})
        before.add_gate(Gate("U0", "or", ["a", "b"], "d"))
        before.add_dff(DFF("FF0", d="d", q="q", clk="clk"))
        after = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"q"})
        after.add_gate(Gate("N0", "not", ["a"], "na"))
        after.add_gate(Gate("N1", "not", ["b"], "nb"))
        after.add_gate(Gate("N2", "nand", ["na", "nb"], "d"))
        after.add_dff(DFF("FF0", d="d", q="q", clk="clk"))

        selected = validator._selected_outputs_for_large_transform(
            before,
            after,
            "optimize_cone",
            {"target": "q", "allowed_gates": ["nand", "not"]},
        )
        residual = validator._check_transform_residual(
            after,
            "optimize_cone",
            {"target": "q", "allowed_gates": ["nand", "not"]},
        )

        self.assertEqual(selected, ["d"])
        self.assertEqual(residual[0], "PASS")
        self.assertIn('resolved to "d"', residual[1])

    def test_large_cone_gate_library_residual_rejects_disallowed_gate(self) -> None:
        design = Design(module_name="top", inputs={"a", "b", "clk"}, outputs={"q"})
        design.add_gate(Gate("U0", "or", ["a", "b"], "d"))
        design.add_dff(DFF("FF0", d="d", q="q", clk="clk"))

        result = validator._check_transform_residual(
            design,
            "optimize_cone",
            {"target": "q", "allowed_gates": ["nand", "not"]},
        )

        self.assertEqual(result[0], "FAIL")
        self.assertIn("disallowed gate types", result[1])

    def test_identity_gate_removal_compositional_proof(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        before.add_gate(Gate("I0", "and", ["a", "a"], "n0"))
        before.add_gate(Gate("I1", "or", ["n0", "n0"], "n1"))
        before.add_gate(Gate("U0", "and", ["n1", "b"], "y"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        after.add_gate(Gate("U0", "and", ["a", "b"], "y"))

        result = validator._check_large_compositional_transform(
            before,
            after,
            "optimize_design_depth",
            {"allowed_gates": ["and", "not"]},
        )

        self.assertEqual(result[0], "PASS")
        self.assertIn("2 degenerate", result[1])
        self.assertIn("whole design uses only", result[1])

    def test_double_inverter_removal_compositional_proof(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        before.add_gate(Gate("N0", "not", ["a"], "n0"))
        before.add_gate(Gate("N1", "not", ["n0"], "n1"))
        before.add_gate(Gate("U0", "and", ["n1", "b"], "y"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y"})
        after.add_gate(Gate("U0", "and", ["a", "b"], "y"))

        result = validator._check_large_compositional_transform(
            before,
            after,
            "optimize_design_depth",
            {"allowed_gates": ["and", "not"]},
        )

        self.assertEqual(result[0], "PASS")
        self.assertIn("1 double-inverter", result[1])
        self.assertIn("whole design uses only", result[1])

    def test_double_inverter_removal_rejects_shared_intermediate(self) -> None:
        before = Design(module_name="top", inputs={"a", "b"}, outputs={"y", "tap"})
        before.add_gate(Gate("N0", "not", ["a"], "n0"))
        before.add_gate(Gate("N1", "not", ["n0"], "n1"))
        before.add_gate(Gate("U0", "and", ["n1", "b"], "y"))
        before.add_gate(Gate("U_tap", "buf", ["n0"], "tap"))
        after = Design(module_name="top", inputs={"a", "b"}, outputs={"y", "tap"})
        after.add_gate(Gate("U0", "and", ["a", "b"], "y"))
        after.add_gate(Gate("U_tap", "buf", ["a"], "tap"))

        result = validator._check_large_compositional_transform(
            before,
            after,
            "optimize_design_depth",
            {"allowed_gates": ["and", "not", "buf"]},
        )

        self.assertEqual(result[0], "FAIL")
        self.assertIn("non-chain load", result[1])

    def test_deterministic_transform_residual_proofs(self) -> None:
        and_not = Design(module_name="top", inputs={"a"}, outputs={"y"})
        and_not.add_gate(Gate("U0", "and", ["a", "a"], "y"))
        nand_only = Design(module_name="top", inputs={"a"}, outputs={"y"})
        nand_only.add_gate(Gate("U0", "nand", ["a", "a"], "y"))
        duplicate_free = Design(module_name="top", inputs={"a"}, outputs={"y"})
        duplicate_free.add_gate(Gate("U0", "buf", ["a"], "y"))

        self.assertEqual(validator._check_transform_residual(and_not, "replace_with_and_not", {})[0], "PASS")
        self.assertEqual(
            validator._check_transform_residual(nand_only, "replace_nand_const1_with_not", {})[0],
            "PASS",
        )
        self.assertEqual(validator._check_transform_residual(nand_only, "replace_and_not_with_nand", {})[0], "PASS")
        self.assertEqual(validator._check_transform_residual(duplicate_free, "merge_equivalent_gates", {})[0], "PASS")

    def test_large_last_transform_equivalence_uses_alpha_rename_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "rename_equivalence"
            snapshot_dir = case_dir / "snapshots"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "before.v").write_text(
                "module top(a, y); input a; output y; wire old_net; buf U0(old_net, a); not U1(y, old_net); endmodule",
                encoding="utf-8",
            )
            (snapshot_dir / "after.v").write_text(
                "module top(a, y); input a; output y; wire new_net; buf U0(new_net, a); not U1(y, new_net); endmodule",
                encoding="utf-8",
            )
            record = {
                "body": (
                    "Equivalent to the pre-transformation netlist. "
                    "Checked with structural_alpha_rename engine over combinational boundaries."
                ),
                "last_transform_input_snapshot": "snapshots/before.v",
                "after_snapshot": "snapshots/after.v",
                "last_transform": {
                    "transform": "rename_net",
                    "result": {"old_net": "old_net", "new_net": "new_net"},
                },
            }
            ledger_path = case_dir / "ledger.jsonl"

            with patch.object(validator, "check_design_equivalence", side_effect=AssertionError("too expensive")):
                result = validator._validate_last_transform_input_equivalence(
                    release_dir,
                    ledger_path,
                    "rename_equivalence",
                    1,
                    record["body"],
                    record,
                )

            self.assertEqual(result.status, "PASS")
            self.assertIn("alpha-equivalence", result.detail)

    def test_boolean_equation_validator_checks_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            case_dir = release_dir / "runner_output" / "rule" / "validation" / "equation"
            snapshot_dir = case_dir / "snapshots"
            report_dir = release_dir / "output" / "reports"
            snapshot_dir.mkdir(parents=True)
            report_dir.mkdir(parents=True)
            (snapshot_dir / "design.v").write_text(
                (
                    "module top(a, b, y); input a, b; output y; wire n; "
                    "and U0(n, a, b); not U1(y, n); endmodule"
                ),
                encoding="utf-8",
            )
            report_path = report_dir / "equation_y_boolean_equation.txt"
            report_lines = [
                'Boolean equation DAG for "y"',
                "boundary: primary inputs and constants",
                "DFF handling: DFF Q references are expanded to their D input cones",
                "sequential feedback default: 1'b0",
                "equation_count: 2",
                "equations:",
                "1. n = (a & b) # gate=U0, type=and",
                "2. y = !(n) # gate=U1, type=not",
                "final:",
                "final_reference: y",
            ]
            report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
            record = {"after_snapshot": "snapshots/design.v"}
            body = (
                "Complete Boolean equation DAG for \"y\" was written to "
                "output\\reports\\equation_y_boolean_equation.txt.\n"
                "Equation count: 2.\nFinal expression reference: y."
            )

            valid = validator._validate_boolean_equation_derivation(
                release_dir,
                case_dir / "ledger.jsonl",
                "equation",
                1,
                {"target": "y"},
                body,
                record,
            )
            report_path.write_text(
                ("\n".join(report_lines) + "\n").replace("(a & b)", "(a | b)"),
                encoding="utf-8",
            )
            corrupted = validator._validate_boolean_equation_derivation(
                release_dir,
                case_dir / "ledger.jsonl",
                "equation",
                2,
                {"target": "y"},
                body,
                record,
            )

            self.assertEqual(valid.status, "PASS")
            self.assertEqual(corrupted.status, "FAIL")
            self.assertIn("line 7", corrupted.detail)

    def test_large_transform_uses_selected_output_equivalence_when_target_is_output(self) -> None:
        before = Design(module_name="top", inputs={"a"}, outputs={"y", "z"})
        before.add_gate(Gate("U1", "buf", ["a"], "y"))
        before.add_gate(Gate("U2", "not", ["a"], "z"))
        after = Design(module_name="top", inputs={"a"}, outputs={"y", "z"})
        after.add_gate(Gate("U1_rewrite", "buf", ["a"], "y"))
        after.add_gate(Gate("U2", "not", ["a"], "z"))

        result = _validate_large_transform_with_guards(
            "test_large",
            1,
            "optimize_cone",
            {"target": "y"},
            before,
            after,
        )

        self.assertEqual(result.status, "PASS")
        self.assertIn("selected-output equivalence passed", result.detail)

    def test_large_remove_dangling_falls_back_to_full_equivalence(self) -> None:
        before = Design(module_name="top", inputs={"a"}, outputs={"y"})
        before.add_gate(Gate("U1", "buf", ["a"], "y"))
        after = Design(module_name="top", inputs={"a"}, outputs={"y"})
        after.add_gate(Gate("U1_rewrite", "buf", ["a"], "y"))

        result = _validate_large_transform_with_guards(
            "test_large",
            2,
            "remove_dangling",
            {},
            before,
            after,
        )

        self.assertEqual(result.status, "PASS")
        self.assertIn("full-output equivalence passed", result.detail)

    def test_large_transform_allows_preexisting_connectivity_issues_without_regression(self) -> None:
        before = Design(module_name="top", inputs={"a"}, outputs={"y"})
        before.add_gate(Gate("U1", "buf", ["a"], "y"))
        before.add_gate(Gate("U2", "not", ["a"], "y"))
        after = Design(module_name="top", inputs={"a"}, outputs={"y"})
        after.add_gate(Gate("U1", "buf", ["a"], "y"))
        after.add_gate(Gate("U2", "not", ["a"], "y"))

        result = _validate_large_transform_with_guards(
            "test_large",
            3,
            "remove_dangling",
            {},
            before,
            after,
        )

        self.assertEqual(result.status, "PASS")
        self.assertIn("full-output equivalence passed", result.detail)

    def test_large_transform_fails_new_connectivity_regression(self) -> None:
        before = Design(module_name="top", inputs={"a"}, outputs={"y", "z"})
        before.add_gate(Gate("U1", "buf", ["a"], "y"))
        after = Design(module_name="top", inputs={"a"}, outputs={"y", "z"})
        after.add_gate(Gate("U1", "buf", ["a"], "y"))
        after.add_gate(Gate("U2", "not", ["a"], "y"))

        result = _validate_large_transform_with_guards(
            "test_large",
            4,
            "remove_dangling",
            {},
            before,
            after,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertIn("connectivity regression failed", result.detail)
        self.assertIn("new_duplicate_drivers", result.detail)


if __name__ == "__main__":
    unittest.main()
