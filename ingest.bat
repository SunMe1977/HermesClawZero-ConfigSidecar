@echo off
:: Ingest a file into the HermesClawZero sync directory
:: Drop a file onto this script to ingest it into your memory system.

set SYNC_DIR=.\sync
if not exist "%SYNC_DIR%" mkdir "%SYNC_DIR%"

if "%~1"=="" goto :usage

echo Copying %~1 to %SYNC_DIR%...
copy "%~1" "%SYNC_DIR%\"
echo.
echo Success: %~n1%~x1 has been moved to the sync folder.
echo The watchdog will ingest it shortly.
echo.
pause
goto :eof

:usage
echo Usage: Drag and drop a file onto this script.
pause
