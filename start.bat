@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 正在建立虛擬環境...
  python -m venv .venv
  if errorlevel 1 (
    echo 找不到 Python。請先安裝 Python 3.11 以上並勾選 Add to PATH。
    pause
    exit /b 1
  )
)

echo 檢查套件...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo 套件安裝失敗。
  pause
  exit /b 1
)

if not exist "web\dist\index.html" (
  where node >nul 2>&1
  if errorlevel 1 (
    echo 需要 Node.js 才能第一次建置介面。請安裝 https://nodejs.org/
    pause
    exit /b 1
  )
  echo 建置介面（第一次會下載前端套件）...
  pushd web
  call npm install
  if errorlevel 1 (
    echo npm install 失敗。
    popd
    pause
    exit /b 1
  )
  call npm run build
  if errorlevel 1 (
    echo 介面建置失敗。
    popd
    pause
    exit /b 1
  )
  popd
)

echo 啟動本機觀賞（只聽 127.0.0.1:6970）...
".venv\Scripts\python.exe" -m backend
pause
