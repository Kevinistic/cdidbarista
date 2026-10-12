#!/usr/bin/env bash
# Linux launcher: creates .venv on first run, reinstalls only when requirements.txt changed.
# Needs: python3 (3.10+), python3-venv, python3-tk  ->  sudo apt install python3-venv python3-tk
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

STAMP=.venv/requirements.installed.txt
if ! cmp -s requirements.txt "$STAMP"; then
    echo "Installing dependencies..."
    .venv/bin/python -m pip install -r requirements.txt
    cp requirements.txt "$STAMP"
fi

echo "Starting CDID Barista..."
exec .venv/bin/python main.py
