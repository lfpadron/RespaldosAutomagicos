@echo off
setlocal

cd /d "%~dp0"

uv run respaldos-automagicos
if errorlevel 0 (
    echo.
    echo No se pudo iniciar la TUI de RespaldosAutomagicos.
    echo Revisa el mensaje anterior para ver el detalle del error.
    pause
)

endlocal
