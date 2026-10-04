@echo off
setlocal
cd /d "%~dp0"
set "HOST=127.0.0.1"
set "PORT=8088"
set "COOKIE_SECURE=0"
echo Starting ORBIT offline at http://localhost:8088
echo Keep this window open while playing.
python server.py
pause
