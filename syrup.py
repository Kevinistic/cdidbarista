"""Clicks the ordered syrup on the "PILIH RASA" picker (ORDER["syrup"]); see choice.py."""
from choice import ChoicePicker
from config import SYRUPS

picker = ChoicePicker("PILIH RASA", "syrup", SYRUPS)
tick = picker.tick
