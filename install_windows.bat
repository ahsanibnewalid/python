@echo off
setlocal
py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python setup_local.py
echo.
echo Setup complete. Run start_windows.bat to start the local site.
pause
