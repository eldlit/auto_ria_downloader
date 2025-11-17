@echo off
setlocal

if "%PYTHON_BIN%"=="" (
    set "PYTHON_BIN=python"
)

%PYTHON_BIN% -m autoria_parser --clear-cache %*
