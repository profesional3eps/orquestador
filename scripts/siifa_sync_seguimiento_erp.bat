@echo off
REM Backfill ERP desde CSV con seguimiento — consola Windows (sin Docker).
cd /d "%~dp0"
python "%~dp0siifa_sync_seguimiento_erp.py" %*
exit /b %ERRORLEVEL%
