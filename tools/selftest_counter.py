"""Offline counter-boundary and movement checks; hardware input is mocked."""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main
import nav


class CounterTests(unittest.TestCase):
    def cafe(self):
        cafe = nav.Map({'labels': {'Bean Hopper': [0, -1], 'Foam Maker': [0, 1]},
                        'spots': {'start': [-1, 0]}, 'pitch': 0.0, 'D': 1.0})
        return cafe

    def bot(self, x=-1.0):
        bot = main.Bot.__new__(main.Bot)
        bot.held = set()
        bot.enabled, bot.check, bot.grab = Mock(), Mock(), Mock()
        pose = (np.array([x, 0.0]), 0.0, 0.0)
        bot.nav = SimpleNamespace(map=self.cafe(), pose=pose, update=Mock(return_value=pose),
                                  outside=Mock(return_value=False))
        return bot

    def test_blocks_forward_back_and_strafes_toward_counter(self):
        for key, yaw in ((config.FORWARD, 0), (config.BACK, math.pi),
                         (config.STRAFE_LEFT, -math.pi / 2), (config.STRAFE_RIGHT, math.pi / 2)):
            with self.subTest(key=key):
                self.assertFalse(self.cafe().safe_motion([-0.1, 0], yaw, key))
                self.assertTrue(self.cafe().safe_motion([-0.1, 0], yaw + math.pi, key))

    def test_blocks_jump_near_counter(self):
        self.assertFalse(self.cafe().safe_motion([-0.1, 0], 0, config.JUMP))
        self.assertFalse(self.cafe().safe_motion([0.1, 0], 0, config.JUMP))
        self.assertTrue(self.cafe().safe_motion([-1, 0], 0, config.JUMP))

    @patch.object(main, 'send_key')
    def test_counter_guard_sends_no_key_down(self, send):
        bot = self.bot(-0.1)
        with self.assertRaisesRegex(main.Abort, 'counter ahead'):
            bot.tap(config.FORWARD, 0.25)
        send.assert_not_called()

    @patch.object(main, 'send_key')
    def test_missing_fix_does_not_reuse_stale_pose(self, send):
        bot = self.bot()
        bot.nav.update.return_value = None
        with self.assertRaisesRegex(main.Abort, 'no position fix'):
            bot.tap(config.FORWARD, 0.25)
        send.assert_not_called()

    @patch.object(main, 'send_key')
    def test_outside_pauses_for_repositioning(self, send):
        bot = self.bot(1.0)
        bot.nav.outside.return_value = True
        with self.assertRaisesRegex(main.Abort, 'outside the kitchen'):
            bot.tap(config.BACK, 0.25)
        bot.enabled.clear.assert_called_once()
        send.assert_not_called()

    @patch.object(main, 'send_key')
    def test_focus_lost_during_localization_sends_no_input(self, send):
        bot = self.bot()
        bot.check.side_effect = [None, main.Abort('paused')]
        with self.assertRaises(main.Abort):
            bot.tap(config.FORWARD, 0.25)
        send.assert_not_called()

    @patch.object(main.time, 'sleep')
    @patch.object(main, 'send_key')
    def test_long_strafe_is_split_and_localizes_only_while_stopped(self, send, sleep):
        bot = self.bot()

        def fix(img):
            self.assertFalse(bot.held)
            return bot.nav.pose

        bot.nav.update.side_effect = fix
        bot.tap(config.STRAFE_LEFT, 0.6)
        self.assertEqual(bot.nav.update.call_count, 3)
        self.assertTrue(all(c.args[0] <= config.WALK_BURST for c in sleep.call_args_list))
        self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list), 0.6)
        self.assertFalse(bot.held)

    @patch.object(main.time, 'sleep')
    @patch.object(main, 'send_key')
    def test_missing_second_fix_stops_long_movement(self, send, sleep):
        bot = self.bot()
        bot.nav.update.side_effect = [bot.nav.pose, None]
        with self.assertRaises(main.Abort):
            bot.tap(config.FORWARD, 0.6)
        self.assertEqual(send.call_args_list, [unittest.mock.call(config.FORWARD, True),
                                             unittest.mock.call(config.FORWARD, False)])
        self.assertFalse(bot.held)

    @patch.object(main.time, 'sleep', side_effect=RuntimeError('interrupted'))
    @patch.object(main, 'send_key')
    def test_exception_releases_key(self, send, sleep):
        bot = self.bot()
        with self.assertRaises(RuntimeError):
            bot.tap(config.FORWARD, 0.25)
        self.assertFalse(bot.held)
        send.assert_called_with(config.FORWARD, False)

    @patch.object(main.time, 'sleep')
    @patch.object(main, 'send_key')
    def test_turning_can_recover_without_a_fix(self, send, sleep):
        bot = self.bot()
        bot.nav.update.return_value = None
        bot.tap(config.TURN_RIGHT, 0.1)
        bot.nav.update.assert_not_called()
        self.assertFalse(bot.held)

    def test_blocked_map_walk_falls_back_to_chip_search(self):
        bot = self.bot()
        bot.say = Mock()
        bot._goto_map = Mock(side_effect=main.MovementBlocked('counter ahead'))
        self.assertFalse(bot.goto_map('Bean Hopper'))

    def test_map_fallback_does_not_swallow_pause(self):
        bot = self.bot()
        bot._goto_map = Mock(side_effect=main.Abort('paused'))
        with self.assertRaises(main.Abort):
            bot.goto_map('Bean Hopper')

    @patch.object(main.time, 'sleep')
    @patch.object(main, 'find_station_label', return_value=None)
    @patch.object(main, 'highlighted_chip')
    def test_visible_station_chip_skips_map_movement(self, highlighted, label, sleep):
        bot = self.bot()
        chip = highlighted.return_value
        bot.goto_map, bot.navigate = Mock(), Mock()
        self.assertIs(bot.goto_station('Bean Hopper', []), chip)
        self.assertEqual(highlighted.call_count, 2)
        bot.goto_map.assert_not_called()
        bot.navigate.assert_not_called()

    @patch.object(main.time, 'sleep')
    @patch.object(main, 'find_prompt')
    def test_visible_customer_chip_skips_map_movement(self, prompt, sleep):
        bot = self.bot()
        chip = prompt.return_value.chip
        bot.goto_map, bot._goto_text = Mock(), Mock()
        self.assertIs(bot.goto_text(config.ASK_PROMPTS, config.COUNTER_LANDMARKS, map_target='register'), chip)
        self.assertEqual(prompt.call_count, 2)
        bot.goto_map.assert_not_called()
        bot._goto_text.assert_not_called()

    def test_unlearned_register_does_not_scan_for_a_map_fix(self):
        bot = self.bot()
        bot.nav.spots = {}
        bot.localize = Mock()
        self.assertFalse(bot.goto_map('register'))
        bot.localize.assert_not_called()

    def test_outside_spot_is_not_saved_as_a_station_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            navigator = nav.Navigator(self.cafe(), str(Path(tmp) / 'learned.json'))
            navigator.learn('register', [0.1, 0.0])
            self.assertNotIn('register', navigator.spots)
            navigator.learn('register', [-1.0, 0.0])
            self.assertEqual(len(navigator.spots['register']), 1)

    @patch('head.head_width', return_value=None)
    @patch.object(nav, 'observe', return_value=[])
    def test_outside_fix_does_not_grow_kitchen_roadmap(self, observe, head_width):
        with tempfile.TemporaryDirectory() as tmp:
            navigator = nav.Navigator(self.cafe(), str(Path(tmp) / 'learned.json'))
            navigator.map.localize = Mock(return_value=(np.array([0.1, 0.0]), 0.0, 0.0, 0.0))
            count = len(navigator.nodes)
            self.assertIsNotNone(navigator.update(np.zeros((10, 10, 3), np.uint8)))
            self.assertEqual(len(navigator.nodes), count)


if __name__ == '__main__':
    unittest.main()
