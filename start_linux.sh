#!/usr/bin/env bash
set -e
if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run ./install_linux.sh first."
  exit 1
fi
. .venv/bin/activate
if [ ! -f ".env" ]; then
  python setup_local.py
fi
python app.py
