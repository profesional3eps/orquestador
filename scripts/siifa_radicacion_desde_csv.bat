@echo off
REM Redirige a export/ (script y CSV en la misma carpeta).
cd /d "%~dp0..\export"
python "%~dp0..\export\siifa_radicacion_desde_csv.py" %*
exit /b %ERRORLEVEL%
