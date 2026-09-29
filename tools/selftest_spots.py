"""Offline checks for learning spots after sparse station views; sends no game input."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main


class SpotTests(unittest.TestCase):
    def bot(self, fixes):
        bot = main.Bot.__new__(main.Bot)
        bot.nav = SimpleNamespace(update=Mock(side_effect=fixes),
                                  pose=(np.array([1.0, 2.0]), 0.0, 0.0), learn=Mock())
        bot.used_at, bot.used_chip = None, True
        bot.check, bot.grab, bot.tap = Mock(), Mock(), Mock()
        bot.release_all, bot.say = Mock(), Mock()
        return bot

    @patch.object(main.time, 'sleep')
    def test_recovers_after_sparse_view(self, sleep):
        bot_pose = (np.array([1.0, 2.0]), 0.0, 0.0)
        bot = self.bot([None, bot_pose])
        bot.learn_spot('register')
        np.testing.assert_array_equal(bot.nav.learn.call_args.args[1], bot_pose[0])
        self.assertEqual(bot.nav.learn.call_args.args[0], 'register')
        bot.tap.assert_called_once_with(config.TURN_RIGHT, config.LOCALIZE_TURN)
        self.assertTrue(all(c.kwargs == {'panel_every': 0} for c in bot.check.call_args_list))
        self.assertFalse(bot.used_chip)
        self.assertIsNone(bot.used_at)

    def test_keeps_position_at_click(self):
        bot = self.bot([])
        bot.used_at = np.array([3.0, 4.0])
        bot.learn_spot('Cup Rack')
        np.testing.assert_array_equal(bot.nav.learn.call_args.args[1], [3.0, 4.0])
        bot.nav.update.assert_not_called()
        bot.tap.assert_not_called()

    @patch.object(main.time, 'sleep')
    def test_no_fix_does_not_learn_stale_pose(self, sleep):
        bot = self.bot([None] * config.LOCALIZE_TRIES)
        bot.learn_spot('Bean Hopper')
        bot.nav.learn.assert_not_called()
        self.assertFalse(bot.used_chip)
        self.assertIsNone(bot.used_at)

    def test_no_interaction_does_not_learn(self):
        bot = self.bot([])
        bot.used_chip = False
        bot.used_at = np.array([3.0, 4.0])
        bot.learn_spot('Milk')
        bot.nav.learn.assert_not_called()
        bot.nav.update.assert_not_called()
        self.assertIsNone(bot.used_at)

    def test_pause_aborts_recovery_and_clears_pending_spot(self):
        bot = self.bot([])
        bot.check.side_effect = main.Abort('paused')
        with self.assertRaises(main.Abort):
            bot.learn_spot('register')
        bot.tap.assert_not_called()
        bot.nav.learn.assert_not_called()
        self.assertFalse(bot.used_chip)

    def test_no_map_clears_pending_spot(self):
        bot = self.bot([])
        bot.nav = None
        bot.learn_spot('register')
        bot.tap.assert_not_called()
        self.assertFalse(bot.used_chip)

    @patch.object(main, 'panel_text', return_value='Ruined. Bin it (E), then grab a fresh cup')
    @patch.object(main.coffee, 'find_track', return_value=None)
    def test_ruined_drink_does_not_teach_a_successful_spot(self, track, panel):
        bot = self.bot([])
        bot.used_at = np.array([3.0, 4.0])
        self.assertTrue(bot.wait_for_change(main.Step('cup')))
        bot.learn_spot('Cup Rack')
        bot.nav.learn.assert_not_called()
        self.assertFalse(bot.used_chip)


if __name__ == '__main__':
    unittest.main()
