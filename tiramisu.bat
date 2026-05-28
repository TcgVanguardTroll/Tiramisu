@echo off
setlocal

REM Self-locate: TIRAMISU_ROOT is the directory this .bat file lives in
SET "TIRAMISU_ROOT=%~dp0"
IF "%TIRAMISU_ROOT:~-1%"=="\" SET "TIRAMISU_ROOT=%TIRAMISU_ROOT:~0,-1%"

REM Find Python -- prefer the known install, fall back to PATH
SET "PYTHON=C:\Python314\python.exe"
IF NOT EXIST "%PYTHON%" (
    WHERE python >nul 2>&1
    IF ERRORLEVEL 1 (
        echo [tiramisu] Python not found. Add python to PATH or set PYTHON in tiramisu.bat.
        exit /b 1
    )
    SET "PYTHON=python"
)

REM Pass everything through to the dispatcher
%PYTHON% "%TIRAMISU_ROOT%\scripts\dispatch.py" %*

endlocal
