@echo off
REM Radicacion SIIFA desde CSV — ejecutar desde export/ (junto al CSV).
cd /d "%~dp0"
python "%~dp0siifa_radicacion_desde_csv.py" %*
exit /b %ERRORLEVEL%
