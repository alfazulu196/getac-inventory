@echo off
rem Copies the field data folder to a USB drive (end of survey day).
rem Russian instructions: see CHEK_LIST_VYEZD.md, section 4.
rem Usage: backup_data.bat ^<destination^>   e.g. backup_data.bat E:\polevoy_den

if "%~1"=="" (
  echo [X] No destination given. Example: backup_data.bat E:\polevoy_den
  pause
  exit /b 1
)

set "SRC=%~dp0..\data"
if not exist "%SRC%" (
  echo [X] Data folder not found: %SRC%
  pause
  exit /b 1
)

echo Copying %SRC% -^> %~1\data ...
robocopy "%SRC%" "%~1\data" /E /XJ /R:1 /W:1 /NFL /NDL /NP /NJH >nul
if errorlevel 8 (
  echo [X] Copy FAILED. Check the USB drive letter and free space, then retry.
  pause
  exit /b 1
)
echo [OK] Data copied: %~1\data  ^(database, photos, exports, logs^)
echo Do not forget: this folder includes rack_ai_log.jsonl - needed for debrief.
pause
