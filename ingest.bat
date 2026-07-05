@echo off
:: Ingest a file into the HermesClawZero inbox
:: Drop a file onto this script to put it into your brain's inbox.

set INBOX_DIR=.\inbox
if not exist "%INBOX_DIR%" mkdir "%INBOX_DIR%"

if "%~1"=="" goto :usage

echo Moving %~1 to %INBOX_DIR%...
move "%~1" "%INBOX_DIR%\"
echo.
echo Success: %~n1%~x1 has been moved to the inbox.
echo The watchdog will process it shortly.
echo.
pause
goto :eof

:usage
echo Usage: Drag and drop a file onto this script.
pause
