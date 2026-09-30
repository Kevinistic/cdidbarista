"""Offline customer cue checks; only verified prompts can supply interaction chips."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main
import ocr


class CustomerTests(unittest.TestCase):
    def frame(self):
        img = np.zeros((1172, 1920, 3), np.uint8)
        cv2.putText(img, 'Alice', (700, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1)
        cv2.putText(img, '@alice', (690, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1)
        return img

    @patch.object(main, 'read_line', side_effect=[('@alice', 0.9), ('Alice', 0.9)])
    def test_paired_handle_is_navigation_only(self, read):
        target = main.find_customer_label(self.frame())
        self.assertEqual(target.text, 'Alice')
        self.assertIsNone(target.chip)
        self.assertIn('@', read.call_args_list[0].kwargs['allowlist'])

    @patch.object(main, 'read_line', return_value=('Barista', 0.9))
    def test_employee_role_is_not_a_customer_handle(self, read):
        self.assertIsNone(main.find_customer_label(self.frame()))

    @patch.object(main, 'read_line', side_effect=[('@alice', 0.9), ('Alice', 0.9)])
    def test_remembered_customer_does_not_match_a_different_player(self, read):
        self.assertIsNone(main.find_customer_label(self.frame(), 'Bob'))

    @patch.object(main, 'read_line', return_value=('@alice', 0.01))
    def test_uncertain_handle_is_rejected(self, read):
        self.assertIsNone(main.find_customer_label(self.frame()))

    @patch.object(main, 'read_line')
    def test_name_too_far_from_verified_chip_is_not_assigned(self, read):
        self.assertIsNone(main.find_customer_label(self.frame(), near=(1200, 600)))
        read.assert_not_called()

    @patch.object(main, 'find_station_label')
    @patch.object(main, 'find_customer_label')
    def test_person_cue_precedes_equipment_for_counter_search(self, person, station):
        person.return_value = main.Target((500, 200, 600, 220), 'Alice')
        self.assertIs(main.find_landmark(self.frame(), config.COUNTER_LANDMARKS), person.return_value)
        station.assert_not_called()

    @patch.object(main, 'find_station_label')
    @patch.object(main, 'find_customer_label')
    def test_bin_search_does_not_use_customer_cues(self, person, station):
        main.find_landmark(self.frame(), config.BIN_LANDMARKS)
        person.assert_not_called()
        station.assert_called_once()

    @patch.object(main, 'find_customer_label')
    @patch.object(main, 'find_prompt')
    def test_verified_prompt_wins_during_scan(self, prompt, person):
        bot = main.Bot.__new__(main.Bot)
        bot.scan = lambda locate, slow: locate(self.frame(), None)
        bot.approach = Mock()
        self.assertIs(bot._goto_text(config.ASK_PROMPTS, config.COUNTER_LANDMARKS), prompt.return_value.chip)
        bot.approach.assert_not_called()
        person.assert_not_called()

    @patch.object(main, 'find_customer_label')
    @patch.object(main, 'find_prompt', return_value=None)
    def test_person_in_scan_is_approached_not_returned_as_a_chip(self, prompt, person):
        bot = main.Bot.__new__(main.Bot)
        bot.scan = lambda locate, slow: locate(self.frame(), None)
        bot.approach = Mock(return_value=None)
        person.return_value = main.Target((500, 200, 600, 220), 'Alice')
        self.assertIsNone(bot._goto_text(config.ASK_PROMPTS, config.COUNTER_LANDMARKS))
        self.assertIs(bot.approach.call_args.args[0], person.return_value)

    @patch.object(ocr, 'reader')
    def test_optional_handle_allowlist_preserves_default_ocr(self, reader):
        reader.return_value.recognize.return_value = [([], 'Alice', 0.9)]
        img = np.zeros((30, 100), np.uint8)
        ocr.read_line(img, (0, 0, 100, 30))
        self.assertEqual(reader.return_value.recognize.call_args.kwargs['allowlist'], ocr.ALLOWLIST)
        ocr.read_line(img, (0, 0, 100, 30), allowlist=ocr.ALLOWLIST + '@_')
        self.assertEqual(reader.return_value.recognize.call_args.kwargs['allowlist'], ocr.ALLOWLIST + '@_')


if __name__ == '__main__':
    unittest.main()
