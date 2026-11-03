from __future__ import annotations

import unittest

from dma.config import DEFAULT_MAX_STEPS, MAX_MAX_STEPS, Config, normalize_max_steps


class MaxStepsTests(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(Config().normalized().max_steps, DEFAULT_MAX_STEPS)
        self.assertGreater(DEFAULT_MAX_STEPS, 20)

    def test_zero_means_unlimited(self) -> None:
        self.assertEqual(Config(max_steps=0).normalized().max_steps, 0)
        self.assertEqual(normalize_max_steps(0), 0)

    def test_high_value_is_kept(self) -> None:
        self.assertEqual(Config(max_steps=64).normalized().max_steps, 64)

    def test_value_is_clamped(self) -> None:
        self.assertEqual(Config(max_steps=5000).normalized().max_steps, MAX_MAX_STEPS)
        self.assertEqual(Config(max_steps=-3).normalized().max_steps, 0)


if __name__ == "__main__":
    unittest.main()
