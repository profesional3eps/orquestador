@echo off
REM Export SIIFA a CSV — usa el .env de la raiz del proyecto (sin venv obligatorio).
set ROOT=%~dp0..
python "%~dp0siifa_export_facturas_csv.py" --env-file "%ROOT%.env" --verbose %*
exit /b %ERRORLEVEL%
