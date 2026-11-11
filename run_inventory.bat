@echo off
rem Запуск новой версии приложения инвентаризации на ноутбуке.
cd /d "%~dp0"
python run_inventory.py
if errorlevel 1 (
    echo.
    echo Приложение завершилось с ошибкой. Смотрите сообщения выше.
    pause
)
