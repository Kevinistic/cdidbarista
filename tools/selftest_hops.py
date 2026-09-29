"""Offline lost-fix recovery and camera-distance confidence checks."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main
import nav
from mapper import project


class HopTests(unittest.TestCase):
    def bot(self):
        bot = main.Bot.__new__(main.Bot)
        bot.nav = SimpleNamespace(map=SimpleNamespace(D=1.0),
                                  pose=(np.array([0.0, 0.0]), 0.0, 0.0),
                                  update=Mock(), heading_error=Mock(return_value=0.0))
        bot.tap, bot.release, bot.check, bot.say, bot.grab = Mock(), Mock(), Mock(), Mock(), Mock()
        bot.unstick = Mock()
        return bot

    def test_missing_fix_recovers_before_another_step(self):
        bot = self.bot()
        bot.nav.update.return_value = None

        def recover(**kwargs):
            bot.release.assert_called_with(config.FORWARD)
            bot.nav.pose = (np.array([1.0, 0.0]), 0.0, 0.0)
            return True

        bot.localize = Mock(side_effect=recover)
        self.assertTrue(bot.walk_to(np.array([1.0, 0.0])))
        bot.localize.assert_called_once()
        bot.tap.assert_called_once_with(config.FORWARD, config.WALK_BURST)

    def test_guard_lost_fix_also_recovers_without_motion(self):
        bot = self.bot()
        bot.tap.side_effect = main.LostFix('missing')
        bot.localize = Mock(return_value=False)
        self.assertFalse(bot.walk_to(np.array([1.0, 0.0])))
        bot.localize.assert_called_once()
        bot.nav.update.assert_not_called()
        bot.release.assert_called_with(config.FORWARD)

    def test_counter_block_does_not_trigger_recovery_walks(self):
        bot = self.bot()
        bot.tap.side_effect = main.MovementBlocked('counter')
        bot.localize = Mock()
        with self.assertRaises(main.MovementBlocked):
            bot.walk_to(np.array([1.0, 0.0]))
        bot.localize.assert_not_called()
        bot.release.assert_called_with(config.FORWARD)

    def test_recovery_count_is_bounded(self):
        bot = self.bot()
        bot.nav.update.return_value = None
        bot.localize = Mock(return_value=True)
        self.assertFalse(bot.walk_to(np.array([1.0, 0.0])))
        self.assertEqual(bot.localize.call_count, config.MAP_RECOVERIES)

    def test_failed_heading_confirmation_prevents_walking(self):
        bot = self.bot()
        bot.nav.heading_error.return_value = 1.0
        bot.face = Mock(return_value=False)
        self.assertFalse(bot.walk_to(np.array([1.0, 0.0])))
        bot.tap.assert_not_called()

    def test_face_missing_fix_scans_instead_of_rereading_same_view(self):
        bot = self.bot()
        bot.nav.update.return_value = None
        bot.localize = Mock(return_value=False)
        self.assertFalse(bot.face(np.array([1.0, 0.0])))
        bot.nav.update.assert_called_once()
        bot.localize.assert_called_once()

    def test_localization_respects_hop_deadline(self):
        bot = self.bot()
        self.assertFalse(bot.localize(deadline=0.0))
        bot.tap.assert_not_called()
        bot.nav.update.assert_not_called()

    def test_headless_fix_reports_unknown_character_distance(self):
        labels = {'A': [5.0, 0.0], 'B': [6.0, 2.0], 'C': [5.0, 4.0], 'D': [7.0, 3.0]}
        cafe = nav.Map({'labels': labels, 'spots': {}, 'D': 1.0,
                        'pitch': math.radians(20), 'head_k': 0.04})
        obs = [(name, *project((*xy, 2.2), (1.0, 2.0, 3.0), 0.0, cafe.pitch, 1920, 1171), 1.0)
               for name, xy in labels.items()]
        measured = cafe.localize(obs, 1920, 1171, head_w=0.04)
        headless = cafe.localize(obs, 1920, 1171)
        self.assertAlmostEqual(measured[2], headless[2])
        np.testing.assert_allclose(measured[0], headless[0], atol=1e-5)
        self.assertGreaterEqual(headless[3], config.LOCALIZE_NO_HEAD_SPREAD * cafe.D)
        self.assertGreater(headless[3], measured[3])


if __name__ == '__main__':
    unittest.main()
