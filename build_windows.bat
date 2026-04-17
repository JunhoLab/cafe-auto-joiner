@echo off
setlocal

cd /d %~dp0

if not exist .venv (
  py -3.11 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

set PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright
python -m playwright install chromium

pyinstaller --noconfirm --clean CafeAutoJoiner.spec

echo.
echo Build complete: dist\CafeAutoJoiner
endlocal
