@echo off
setlocal

REM Self-locate: TIRAMISU_ROOT is the directory this .bat file lives in
SET "TIRAMISU_ROOT=%~dp0"
IF "%TIRAMISU_ROOT:~-1%"=="\" SET "TIRAMISU_ROOT=%TIRAMISU_ROOT:~0,-1%"

REM Find Python — prefer the known install, fall back to PATH
SET "PYTHON=C:\Python314\python.exe"
IF NOT EXIST "%PYTHON%" (
    WHERE python >nul 2>&1
    IF ERRORLEVEL 1 (
        echo [tiramisu] Python not found. Add python to PATH or set PYTHON in t.bat.
        exit /b 1
    )
    SET "PYTHON=python"
)

IF "%1"=="" GOTO usage
IF /I "%1"=="hook"   GOTO hook
IF /I "%1"=="task"   GOTO task
IF /I "%1"=="review" GOTO review
IF /I "%1"=="scan"   GOTO scan
IF /I "%1"=="pr"     GOTO pr
IF /I "%1"=="help"   GOTO usage
GOTO unknown

:hook
REM  t hook           -- install into current directory
REM  t hook <path>    -- install into <path>
%PYTHON% "%TIRAMISU_ROOT%\scripts\install_hooks.py" %~2
GOTO end

:task
REM  t task                          -- interactive prompt
REM  t task add dark mode            -- words joined into one description
REM  t task "add dark mode to UI"    -- quoted works too
%PYTHON% "%TIRAMISU_ROOT%\scripts\start_task.py" %2 %3 %4 %5 %6 %7 %8 %9
GOTO end

:review
REM  t review   -- run Cookie on staged diff right now, outside a commit
%PYTHON% "%TIRAMISU_ROOT%\hooks\cookie_review.py"
GOTO end

:scan
REM  t scan            -- scan current directory
REM  t scan <path>     -- scan a file or directory
%PYTHON% "%TIRAMISU_ROOT%\scripts\scan.py" %~2
GOTO end

:pr
REM  t pr              -- review all changes vs main/master
REM  t pr <branch>     -- review all changes vs a specific branch
%PYTHON% "%TIRAMISU_ROOT%\scripts\pr_review.py" %~2
GOTO end

:usage
echo.
echo   Tiramisu  --  t ^<command^> [args]
echo.
echo   t hook [path]      Install Cookie + Eclair hooks (default: current dir)
echo   t task [desc]      Croissant scope session before you start coding
echo   t review           Cookie reviews staged diff (outside a commit)
echo   t scan [path]      Cookie reads a file or directory in full
echo   t pr [base]        Cookie reviews your whole branch vs main
echo   t help             This message
echo.
GOTO end

:unknown
echo [tiramisu] Unknown command: %1
echo            Run: t help
exit /b 1

:end
endlocal
