@echo off
rem Ollama health check for the field laptop (V110). Keep on the USB stick.
rem Russian instructions: see CHEK_LIST_VYEZD.md on this USB stick.
echo === Ollama health check ===
echo.

where ollama >nul 2>nul
if errorlevel 1 (
  echo [X] ollama is NOT installed. Run OllamaSetup.exe from the USB stick, then retry.
  pause
  exit /b 1
)

for /f "delims=" %%v in ('ollama --version 2^>nul') do echo [OK] %%v
echo.
echo === Installed models: expecting qwen2.5vl:3b ===
ollama list
echo.
netstat -ano | findstr :11434 | findstr LISTENING >nul
if errorlevel 1 (
  echo [!] Port 11434 is NOT listening. Start ollama: tray icon or run "ollama serve".
) else (
  echo [OK] Ollama service is running on 127.0.0.1:11434
)
echo.
echo === Verdict ===
echo Model listed   - all set, run the inventory app.
echo Model missing  - copy the USB "models" folder to %USERPROFILE%\.ollama\models
echo                 ^(both blobs\ and manifests\^) or, with internet: ollama pull qwen2.5vl:3b
pause
