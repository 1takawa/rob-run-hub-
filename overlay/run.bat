@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv ".venv" >> "setup.log" 2>&1
    if errorlevel 1 (
        echo Не удалось создать окружение Python. Установи 64-битный Python 3.12 или новее.>>"setup.log"
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import importlib.util,sys; names=('PIL','psutil','pystray','win32gui','win32process'); sys.exit(any(importlib.util.find_spec(name) is None for name in names))" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt >> "setup.log" 2>&1
    if errorlevel 1 goto :install_error
)

start "" /b ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:install_error
echo Не удалось установить зависимости. Проверь интернет-соединение и повтори запуск.>>"setup.log"
exit /b 1
