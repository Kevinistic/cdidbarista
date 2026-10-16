"""Clicks the ordered syrup on the flavour picker (ORDER["syrup"]); see choice.py."""
from choice import ChoicePicker
from config import SYRUPS, PICK_TITLE_FLAVOUR

picker = ChoicePicker(PICK_TITLE_FLAVOUR, "syrup", SYRUPS)
tick = picker.tick
