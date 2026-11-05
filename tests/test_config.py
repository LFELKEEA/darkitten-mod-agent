from __future__ import annotations

import unittest

from dma.config import DEFAULT_MAX_STEPS, MAX_MAX_STEPS, Config, normalize_max_steps


class ContextTurnsTests(unittest.TestCase):
    def test_default_is_32(self) -> None:
        self.assertEqual(Config().normalized().context_turns, 32)

    def test_old_default_is_upgraded(self) -> None:
        self.assertEqual(Config(context_turns=12).normalized().context_turns, 32)

    def test_chosen_value_is_kept_after_upgrade(self) -> None:
        config = Config(context_turns=10, context_turns_upgraded=True).normalized()
        self.assertEqual(config.context_turns, 10)

    def test_large_value_is_kept_or_clamped(self) -> None:
        self.assertEqual(Config(context_turns=64).normalized().context_turns, 64)
        self.assertEqual(Config(context_turns=999).normalized().context_turns, 200)


class AutoBuildTests(unittest.TestCase):
    def test_default_is_on(self) -> None:
        self.assertTrue(Config().normalized().auto_build)

    def test_disabled_is_kept(self) -> None:
        self.assertFalse(Config(auto_build=False).normalized().auto_build)


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
