@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if not defined SCRIPT_DIR set "SCRIPT_DIR=."
pushd "%SCRIPT_DIR%" >nul

if defined PYTHONPATH (
    set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%SCRIPT_DIR%src"
)

if "%PYTHON_BIN%"=="" (
    if exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
        set "PYTHON_BIN=%SCRIPT_DIR%\.venv\Scripts\python.exe"
    ) else (
        set "PYTHON_BIN=python"
    )
)

set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "INPUT_PATH=%SCRIPT_DIR%input.txt"

"%PYTHON_BIN%" -m autoria_parser --clear-cache --config "%CONFIG_PATH%" --input "%INPUT_PATH%" %*
set "EXIT_CODE=%ERRORLEVEL%"

popd >nul

if "%~1"=="" (
    echo.
    echo Process finished with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
