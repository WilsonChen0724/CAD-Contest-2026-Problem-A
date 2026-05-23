from __future__ import annotations

import unittest

from eda.design import Design, Gate
from eda.graph import rebuild_graph
from eda.transform import remove_dangling, replace_buffers_with_and


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

    def test_stub_transform_still_rebuilds_graph(self) -> None:
        design = Design(inputs={"a"}, outputs={"y"})
        design.add_gate(Gate(name="U0", type="buf", inputs=["a"], output="y"))
        design.drivers.clear()
        design.fanouts.clear()

        remove_dangling(design)

        self.assertEqual(design.drivers["a"], "PI:a")
        self.assertIn("PO:y", design.fanouts["y"])


if __name__ == "__main__":
    unittest.main()
