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
IF /I "%1"=="hook"      GOTO hook
IF /I "%1"=="task"      GOTO task
IF /I "%1"=="review"    GOTO review
IF /I "%1"=="scan"      GOTO scan
IF /I "%1"=="pr"        GOTO pr
IF /I "%1"=="implement" GOTO implement
IF /I "%1"=="chat"      GOTO chat
IF /I "%1"=="learn"     GOTO learn
IF /I "%1"=="reflect"   GOTO reflect
IF /I "%1"=="research"  GOTO research
IF /I "%1"=="help"      GOTO usage
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
REM  t pr                       -- local terminal review vs main/master
REM  t pr <branch>              -- compare vs a specific base branch
REM  t pr --post                -- also post inline comments to the GitHub PR
REM  t pr --post --dry-run      -- generate and show, don't actually post
%PYTHON% "%TIRAMISU_ROOT%\scripts\pr_review.py" %2 %3 %4 %5
GOTO end

:implement
REM  t implement "description"           -- Eclair writes code with full codebase access
REM  t implement --auto "..."            -- skip confirmation on file writes
REM  t implement --yes "..."             -- skip ALL confirmations
%PYTHON% "%TIRAMISU_ROOT%\scripts\implement.py" %2 %3 %4 %5 %6 %7 %8 %9
GOTO end

:chat
REM  t chat                              -- conversational read-only mode (memory + tools)
REM  t chat <initial question>           -- start with a kicked-off question
%PYTHON% "%TIRAMISU_ROOT%\scripts\chat.py" %2 %3 %4 %5 %6 %7 %8 %9
GOTO end

:learn
REM  t learn "text"         -- teach the agents a preference
REM  t learn list           -- show active preferences
REM  t learn forget <id>    -- deactivate a preference
%PYTHON% "%TIRAMISU_ROOT%\scripts\learn.py" %2 %3 %4 %5 %6 %7 %8 %9
GOTO end

:reflect
REM  t reflect          -- 30-day self-improvement report
REM  t reflect 7        -- last N days
%PYTHON% "%TIRAMISU_ROOT%\scripts\reflect.py" %~2
GOTO end

:research
REM  t research                          -- show latest unread findings/candidates
REM  t research run                      -- force a scan of watched sources now
REM  t research discover                 -- scout GitHub Trending + HN for new candidates
REM  t research ingest <path>            -- ingest a PDF / .md / .txt / .rst file or dir
REM  t research grab <arxiv-id>          -- download an arxiv paper into the library
REM  t research grab --all               -- download every arxiv candidate from latest scan
REM  t research library                  -- list files in your library
REM  t research all                      -- run + discover + ingest-library in one shot
REM  t research mute                     -- mark all pending as read
REM  t research list                     -- list historical findings + candidates
REM  t research sources list             -- show active sources
REM  t research sources add <url> [name] [focus]   -- add a source
REM  t research sources remove <name>    -- remove a source
REM  t research sources reset            -- write defaults to user file
REM  t research sources show             -- dump raw sources.json
%PYTHON% "%TIRAMISU_ROOT%\scripts\research.py" %2 %3 %4 %5 %6 %7 %8 %9
GOTO end

:usage
echo.
echo   Tiramisu  --  t ^<command^> [args]
echo.
echo   t hook [path]       Install Cookie + Eclair hooks (default: current dir)
echo   t task [desc]       Croissant scope session before you start coding
echo   t review            Cookie reviews staged diff (outside a commit)
echo   t scan [path]       Cookie reads a file or directory in full
echo   t pr [base]         Cookie reviews your whole branch vs main
echo   t pr --post         ...and posts inline comments to the GitHub PR
echo   t implement "..."   Eclair writes code with full codebase access
echo   t chat [question]   Conversational mode -- read-only, remembers context
echo   t learn "text"      Teach the agents a preference
echo   t reflect [days]    Weekly self-improvement report
echo   t research [action] Cannoli's external research; auto-runs weekly
echo   t help              This message
echo.
GOTO end

:unknown
echo [tiramisu] Unknown command: %1
echo            Run: t help
exit /b 1

:end
endlocal
