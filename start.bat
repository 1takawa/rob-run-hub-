@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Hidden pool - запуск
if not exist "profiles\logs" mkdir "profiles\logs" >nul 2>&1
set "LOG=%~dp0profiles\logs\start.log"
echo Hidden pool · ROB RUN edition
echo.

rem ---- 1. Python ----------------------------------------------------------------------------------------------
set "BASE="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if not errorlevel 1 goto :have_venv
  echo Окружение .venv повреждено — создаю заново.
  rmdir /s /q ".venv" >nul 2>&1
)
echo [1/4] Ищу Python 3.11–3.13...
for %%V in (3.12 3.13 3.11) do (
  if not defined BASE (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BASE=py -%%V"
  )
)
if not defined BASE (
  python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) and sys.maxsize > 2**32 else 1)" >nul 2>&1
  if not errorlevel 1 set "BASE=python"
)
if not defined BASE goto :no_python
echo       найден: !BASE!
echo [2/4] Создаю окружение .venv ...
!BASE! -m venv .venv >>"%LOG%" 2>&1
if errorlevel 1 goto :venv_failed

:have_venv
set "PY=%~dp0.venv\Scripts\python.exe"

rem ---- 2. Packages: installed again only when requirements change --------------------------------------------------
"%PY%" -c "import hashlib,pathlib,sys; t=b''.join(pathlib.Path(p).read_bytes() for p in ('requirements.txt','overlay/requirements.txt')); m=pathlib.Path('.venv/requirements.sha'); sys.exit(0 if m.exists() and m.read_text()==hashlib.sha1(t).hexdigest() else 1)" >nul 2>&1
if not errorlevel 1 goto :check
echo [3/4] Ставлю библиотеки (первый раз — несколько минут, нужен интернет)...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip >>"%LOG%" 2>&1
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt -r overlay\requirements.txt >>"%LOG%" 2>&1
if errorlevel 1 goto :pip_failed
"%PY%" -c "import hashlib,pathlib; t=b''.join(pathlib.Path(p).read_bytes() for p in ('requirements.txt','overlay/requirements.txt')); pathlib.Path('.venv/requirements.sha').write_text(hashlib.sha1(t).hexdigest())"

:check
echo [4/4] Проверяю и запускаю...
set "PYTHONPATH=%~dp0src"
"%PY%" -c "import cv2, numpy, pynput, pyautogui, winrt.windows.media.ocr, win32gui, pystray; import harvest_assistant.app" >>"%LOG%" 2>&1
if errorlevel 1 goto :check_failed
start "" "%~dp0.venv\Scripts\pythonw.exe" -m harvest_assistant
exit /b 0

rem ---- what went wrong, in words -------------------------------------------------------------------------------
:no_python
echo.
echo Не найден Python 3.11, 3.12 или 3.13 (64-bit). Лучше всего подходит 3.12.
where winget >nul 2>&1
if errorlevel 1 goto :manual_python
choice /c YN /m "Установить Python 3.12 автоматически (winget)?"
if errorlevel 2 goto :manual_python
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :manual_python
echo.
echo Python установлен. Закрой это окно и запусти start.bat ещё раз.
pause
exit /b 0

:manual_python
echo Скачай «Windows installer (64-bit)» Python 3.12 и при установке отметь «Add python.exe to PATH».
start "" "https://www.python.org/downloads/release/python-3128/"
pause
exit /b 1

:venv_failed
echo Не удалось создать окружение Python. Подробности: profiles\logs\start.log
pause
exit /b 1

:pip_failed
echo Не удалось поставить библиотеки. Проверь интернет и запусти ещё раз.
echo Подробности: profiles\logs\start.log
pause
exit /b 1

:check_failed
echo Программа не запустилась. Последние строки журнала:
echo.
powershell -NoProfile -Command "Get-Content -Tail 15 '%LOG%'"
echo.
echo Если ошибка про winrt или cv2 — поставь Python 3.12 (64-bit) и удали папку .venv.
pause
exit /b 1
