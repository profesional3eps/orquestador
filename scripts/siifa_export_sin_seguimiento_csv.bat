@echo off
REM Facturas SIIFA sin seguimiento (TieneRadicado=false). Solo SIIFA, sin ERP.
set ROOT=%~dp0..
python "%~dp0siifa_export_sin_seguimiento_csv.py" --env-file "%ROOT%.env" --verbose %*
exit /b %ERRORLEVEL%
