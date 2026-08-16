@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Streamlit 기본 포트. 다른 것과 겹치면 여기만 고치면 됩니다.
set PORT=8501
set VENV=.venv
set PY=%VENV%\Scripts\python.exe

echo [파워링크 노출순위 - Streamlit] 시작합니다...
echo.

rem ── 1. 파이썬 찾기 ──────────────────────────────────────────────
rem PATH 의 python 은 Microsoft Store 스텁일 수 있어 py 런처를 먼저 본다.
set BOOT=
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set BOOT=py -3
if not defined BOOT (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set BOOT=python
)
if not defined BOOT goto NOPYTHON

rem ── 1.5 경로 길이 확인 ─────────────────────────────────────────
rem streamlit 패키지 안에 140자짜리 경로가 들어 있다. 프로젝트가 깊은 폴더에
rem 있으면 설치 도중 'WinError 206 파일 이름이 너무 깁니다' 로 조용히 실패하고,
rem streamlit 만 빠진 채 나머지가 깔려 원인을 찾기 어려워진다.
set PROJLEN=0
for /f %%L in ('powershell -NoProfile -Command "$env:CD.Length" 2^>nul') do set PROJLEN=%%L
if %PROJLEN% GTR 95 goto PATHTOOLONG

rem ── 2. 가상환경 ────────────────────────────────────────────────
if exist "%PY%" (
    echo [1/2] 가상환경 확인됨 - 건너뜁니다.
) else (
    echo [1/2] 가상환경을 만듭니다. 처음 한 번만 걸립니다...
    %BOOT% -m venv "%VENV%"
    if errorlevel 1 goto VENVFAIL
    if not exist "%PY%" goto VENVFAIL
)

rem ── 3. 패키지 ──────────────────────────────────────────────────
"%PY%" -c "import streamlit, requests, pandas, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo [2/2] 패키지를 설치합니다. 처음에는 몇 분 걸릴 수 있습니다...
    "%PY%" -m pip install --upgrade pip >nul 2>nul
    if exist "requirements.lock" (
        "%PY%" -m pip install -r requirements.lock
    ) else (
        "%PY%" -m pip install -r requirements.txt
    )
    if errorlevel 1 goto PIPFAIL
    "%PY%" -c "import streamlit, requests, pandas, openpyxl" >nul 2>nul
    if errorlevel 1 goto PIPFAIL
) else (
    echo [2/2] 패키지 확인됨 - 건너뜁니다.
)

echo.
echo 브라우저가 http://localhost:%PORT% 으로 열립니다.
echo 종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.

"%PY%" -m streamlit run streamlit_app.py --server.port %PORT% --browser.gatherUsageStats false

pause
exit /b 0

:NOPYTHON
echo.
echo [오류] 파이썬을 찾을 수 없습니다.
echo.
echo   https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요.
echo   설치 화면 맨 아래 'Add python.exe to PATH' 를 체크해야 합니다.
echo.
pause
exit /b 1

:VENVFAIL
echo.
echo [오류] 가상환경을 만들지 못했습니다. %VENV% 폴더를 지우고 다시 실행해 보세요.
echo.
pause
exit /b 1

:PIPFAIL
echo.
echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.
echo.
pause
exit /b 1

:PATHTOOLONG
echo.
echo [오류] 폴더 경로가 너무 깁니다 ^(%PROJLEN%자^).
echo.
echo   %CD%
echo.
echo   streamlit 설치 파일 안에 긴 경로가 있어, 이대로는 설치가 실패합니다.
echo   프로젝트 폴더를 짧은 경로로 옮긴 뒤 다시 실행하세요. 예: C:\work\DdaengJu
echo.
pause
exit /b 1
