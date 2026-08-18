#!/usr/bin/env bash
# 네이버 파워링크 노출순위 — macOS / Linux 실행 스크립트
#
#   chmod +x run_streamlit.sh      (처음 한 번만)
#   ./run_streamlit.sh
#
# 윈도우는 run_streamlit.bat 을 쓰세요. 하는 일은 같습니다.
set -uo pipefail

# 스크립트가 어디에서 실행되든 프로젝트 폴더를 기준으로 삼는다
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${PORT:-8501}"
VENV=".venv"

# 가상환경의 파이썬 위치. macOS/Linux 는 bin/, 윈도우 Git Bash 는 Scripts/ 다.
venv_python() {
    if [ -x "$VENV/bin/python" ]; then
        echo "$VENV/bin/python"
    elif [ -x "$VENV/Scripts/python.exe" ]; then
        echo "$VENV/Scripts/python.exe"
    fi
}
PY="$(venv_python)"

echo "[파워링크 노출순위 - Streamlit] 시작합니다..."
echo

die() { echo; echo "[오류] $1"; echo; exit 1; }

# ── 1. 파이썬 찾기 ────────────────────────────────────────────────
# macOS 의 'python' 은 없거나 2.x 를 가리킬 수 있어 python3 를 먼저 본다.
BOOT=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
        BOOT="$cand"; break
    fi
done
if [ -z "$BOOT" ]; then
    die "파이썬 3.9 이상을 찾을 수 없습니다.

  macOS 라면 둘 중 하나로 설치하세요.
    xcode-select --install          (애플 기본 도구, 가장 간단)
    brew install python             (Homebrew 가 있다면)

  설치한 뒤 이 스크립트를 다시 실행하세요."
fi

# ── 1.5 (윈도우 Git Bash 한정) 경로 길이 ──────────────────────────
# streamlit 패키지 안에 140자짜리 경로가 있어, 깊은 폴더에서는 설치가
# WinError 206 으로 실패한다. macOS/Linux 는 한계가 훨씬 커서 해당 없다.
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*)
        if [ "${#PWD}" -gt 95 ]; then
            die "폴더 경로가 너무 깁니다 (${#PWD}자).

  $PWD

  streamlit 설치 파일 안에 긴 경로가 있어 이대로는 설치가 실패합니다.
  프로젝트를 짧은 경로로 옮긴 뒤 다시 실행하세요. 예: /c/work/DdaengJu"
        fi
        ;;
esac

# ── 2. 가상환경 (없을 때만 만든다) ────────────────────────────────
if [ -n "$PY" ]; then
    echo "[1/2] 가상환경 확인됨 - 건너뜁니다."
else
    echo "[1/2] 가상환경을 만듭니다. 처음 한 번만 걸립니다..."
    "$BOOT" -m venv "$VENV" || die "가상환경을 만들지 못했습니다. $VENV 폴더를 지우고 다시 실행해 보세요."
    PY="$(venv_python)"
    [ -n "$PY" ] || die "가상환경은 만들어졌는데 파이썬 실행 파일이 없습니다. $VENV 를 지우고 다시 실행하세요."
fi

# ── 3. 패키지 (실제로 import 되는지로 판정한다) ───────────────────
# pip list 파싱은 배포판 이름과 import 이름이 다른 경우가 있어 어긋난다.
NEED=1
if "$PY" -c "import streamlit, requests, pandas, openpyxl" >/dev/null 2>&1; then
    NEED=0
fi

if [ "$NEED" -eq 1 ]; then
    echo "[2/2] 패키지를 설치합니다. 처음에는 몇 분 걸릴 수 있습니다..."
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1
    REQ="requirements.txt"
    [ -f requirements.lock ] && REQ="requirements.lock"
    if ! "$PY" -m pip install -r "$REQ"; then
        die "패키지 설치에 실패했습니다.

  인터넷 연결을 확인하고 다시 실행하세요.
  계속 실패하면 $VENV 폴더를 지운 뒤 다시 실행하면 처음부터 받습니다.
  위 로그에 'File name too long' 이나 'WinError 206' 이 보이면
  폴더 경로가 너무 깁니다. 짧은 경로로 옮기세요."
    fi
    # 설치가 끝났다고 끝난 게 아니다. 실제로 import 되는지 다시 확인한다.
    "$PY" -c "import streamlit, requests, pandas, openpyxl" >/dev/null 2>&1 \
        || die "설치는 끝났는데 패키지를 불러오지 못합니다. $VENV 를 지우고 다시 실행하세요."
else
    echo "[2/2] 패키지 확인됨 - 건너뜁니다."
fi

echo
echo "브라우저가 http://localhost:$PORT 으로 열립니다."
echo "종료하려면 이 창에서 Ctrl+C 를 누르세요."
echo

exec "$PY" -m streamlit run streamlit_app.py \
    --server.port "$PORT" \
    --browser.gatherUsageStats false
