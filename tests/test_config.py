from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.config import load_config


class ConfigTest(unittest.TestCase):
    def test_load_config_parses_nested_optimization_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        'provider: "openai"',
                        "optimization_limits:",
                        "  large_design_gate_limit: 1234",
                        "  large_cone_gate_limit: 56",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["optimization_limits"]["large_design_gate_limit"], 1234)
        self.assertEqual(config["optimization_limits"]["large_cone_gate_limit"], 56)
        self.assertIn("raw", config)


if __name__ == "__main__":
    unittest.main()
