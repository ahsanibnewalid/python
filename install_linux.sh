#!/usr/bin/env bash
set -e
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python setup_local.py
echo "Setup complete. Run ./start_linux.sh to start the local site."
