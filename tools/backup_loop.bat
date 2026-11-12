@echo off
rem Periodic copy of data\ to a USB drive during the survey day.
rem Usage: backup_loop.bat ^<destination^> [minutes]   default interval: 20
rem Stop: press Ctrl+C or close this window.
rem Russian instructions: see CHEK_LIST_VYEZD.md, section 4.

if "%~1"=="" (
  echo [X] No destination given. Example: backup_loop.bat E:\polevoy_den
  pause
  exit /b 1
)
set "INTERVAL=%~2"
if "%INTERVAL%"=="" set "INTERVAL=20"
set /a WAIT=%INTERVAL%*60
if %WAIT% lss 30 set "WAIT=60"

set "SRC=%~dp0..\data"
if not exist "%SRC%" (
  echo [X] Data folder not found: %SRC%
  pause
  exit /b 1
)

echo Periodic backup: %SRC% -^> %~1\data  every %INTERVAL% min.
echo Keep this window open all day. Stop: Ctrl+C or close the window.

:loop
if not exist "%~1\" mkdir "%~1" 2>nul
robocopy "%SRC%" "%~1\data" /E /XJ /R:1 /W:1 /NFL /NDL /NP /NJH >nul
if errorlevel 8 (
  echo [%time%] [X] Copy FAILED this pass - check the drive and free space.
) else (
  echo [%time%] [OK] Copied.
)
:wait
echo Next pass in %INTERVAL% min...
rem ping = silent delay that works with any stdin redirection (timeout fails)
ping -n %WAIT% 127.0.0.1 >nul
goto loop
