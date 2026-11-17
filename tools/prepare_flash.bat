@echo off
rem Prepares the USB stick for the field trip in one command.
rem Russian instructions: see CHEK_LIST_VYEZD.md, section 1.
rem Usage: prepare_flash.bat ^<drive^>   e.g. prepare_flash.bat E:

python -X utf8 "%~dp0prepare_flash.py" %*
if errorlevel 1 (
  echo [X] USB stick NOT ready - see messages above.
) else (
  echo [OK] USB stick is ready for the field.
)
pause
