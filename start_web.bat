@echo off
setlocal
cd /d "%~dp0"
python start_web.py --host 0.0.0.0 --port 8000 --mode auto --max-concurrent-runs 1
endlocal
