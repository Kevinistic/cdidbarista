"""Clicks the ordered drink on the drink picker (ORDER["menu"]); see choice.py."""
from choice import ChoicePicker
from config import MENU, PICK_TITLE_MENU

picker = ChoicePicker(PICK_TITLE_MENU, "menu", MENU)
tick = picker.tick
