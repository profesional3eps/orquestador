@echo off
cd /d "%~dp0..\export"
python siifa_sync_seguimiento_standalone.py %*
