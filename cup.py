"""Clicks the ordered drink on the "PESANANNYA APA?" picker (ORDER["menu"]); see choice.py."""
from choice import ChoicePicker
from config import MENU

picker = ChoicePicker("PESANANNYA APA?", "menu", MENU)
tick = picker.tick
