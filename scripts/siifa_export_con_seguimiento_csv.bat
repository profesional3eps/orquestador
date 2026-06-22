@echo off
REM Facturas SIIFA con seguimiento (TieneRadicado=true). Solo SIIFA, sin ERP.
set ROOT=%~dp0..
python "%~dp0siifa_export_con_seguimiento_csv.py" --env-file "%ROOT%.env" --verbose %*
exit /b %ERRORLEVEL%
