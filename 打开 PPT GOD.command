#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="8000"
BASE_URL="http://localhost:${PORT}"
URL="${BASE_URL}/"
SOURCE_STAMP="${ROOT_DIR}/.pptgod-data/launcher-source.sha256"

open_pptgod() {
  local launch_url="${URL}?launcher=$(date +%s)"
  if osascript - "$launch_url" >/dev/null 2>&1 <<'APPLESCRIPT'
on run argv
  set targetUrl to item 1 of argv
  tell application "Google Chrome"
    activate
    if (count of windows) is 0 then make new window
    set URL of active tab of front window to targetUrl
  end tell
end run
APPLESCRIPT
  then
    return 0
  fi
  if open "$launch_url" >/dev/null 2>&1; then
    return 0
  fi
  echo "没有成功打开浏览器。请手动访问：${BASE_URL}"
  return 1
}

current_service_ready() {
  local payload
  payload="$(curl -fsS --max-time 5 "${BASE_URL}/agent/readiness" 2>/dev/null)" || return 1
  [[ "$payload" == *'"text_generation"'* && "$payload" == *'"image_generation"'* ]]
}

source_fingerprint() {
  (
    cd "$ROOT_DIR"
    find backend frontend -type f \
      -not -path 'backend/venv/*' \
      -not -path 'frontend/node_modules/*' \
      -not -path 'frontend/dist/*' \
      -not -path '*/__pycache__/*' \
      -not -name '*.pyc' \
      -print \
      | LC_ALL=C sort \
      | while IFS= read -r source_file; do shasum "$source_file"; done
    shasum docker-compose.yml .dockerignore
  ) | shasum | awk '{print $1}'
}

runtime_matches_source() {
  [[ -f "$SOURCE_STAMP" ]] || return 1
  [[ "$(<"$SOURCE_STAMP")" == "$CURRENT_SOURCE_FINGERPRINT" ]]
}

stop_stale_local_pptgod() {
  local listener_pid listener_cwd listener_command
  listener_pid="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1)"
  [[ -n "$listener_pid" ]] || return 0

  listener_cwd="$(lsof -a -p "$listener_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  listener_command="$(ps -p "$listener_pid" -o command= 2>/dev/null || true)"
  if [[ "$listener_cwd" != "${ROOT_DIR}/backend" || "$listener_command" != *"uvicorn app.main:app"* ]]; then
    return 1
  fi

  echo "检测到 PPT GOD 的旧版本地服务，正在安全切换到当前版本..."
  kill "$listener_pid"
  for _ in $(seq 1 20); do
    if ! lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

clear 2>/dev/null || true
echo "正在启动 PPT GOD 本机调试版..."
echo "统一入口：${BASE_URL}"
echo

if ! command -v docker >/dev/null 2>&1; then
  echo "没有检测到 Docker。请先安装并打开 Docker Desktop。"
  echo "下载地址：https://www.docker.com/products/docker-desktop/"
  echo
  read -r -p "按回车关闭窗口。"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "正在打开 Docker Desktop..."
  open -a Docker >/dev/null 2>&1 || true
  for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker 还没启动好。请等 Docker Desktop 运行后，再双击这个文件。"
  echo
  read -r -p "按回车关闭窗口。"
  exit 1
fi

cd "$ROOT_DIR"
CURRENT_SOURCE_FINGERPRINT="$(source_fingerprint)"

if [[ -z "${PPTGOD_HTTPS_PROXY:-}" ]] && lsof -nP -iTCP:7890 -sTCP:LISTEN >/dev/null 2>&1; then
  export PPTGOD_HTTP_PROXY="http://host.docker.internal:7890"
  export PPTGOD_HTTPS_PROXY="http://host.docker.internal:7890"
  export PPTGOD_NO_PROXY="localhost,127.0.0.1,db,redis"
  echo "检测到本机代理端口 7890，Docker 出站请求将通过 host.docker.internal:7890。"
  echo
fi

if current_service_ready; then
  if runtime_matches_source; then
    echo "检测到 PPT GOD 已在运行，正在打开页面..."
    open_pptgod
    echo
    echo "PPT GOD 已启动：${BASE_URL}"
    exit 0
  fi
  echo "检测到项目文件有更新，正在同步到运行版本..."
  echo
elif curl -fsS --max-time 5 "${BASE_URL}/health" >/dev/null 2>&1; then
  if ! stop_stale_local_pptgod; then
    echo "端口 ${PORT} 上运行的不是当前版本的 PPT GOD，也不能安全自动关闭。"
    echo "请关闭占用 http://localhost:${PORT} 的程序后，再双击这个文件。"
    echo
    read -r -p "按回车关闭窗口。"
    exit 1
  fi
fi

echo "正在构建并启动 Docker 服务，首次启动会慢一些..."

PPTGOD_HOST_PORT="$PORT" docker compose up --build -d

echo
echo "正在等待服务就绪..."
for _ in $(seq 1 120); do
  if current_service_ready; then
    mkdir -p "$(dirname "$SOURCE_STAMP")"
    printf '%s\n' "$CURRENT_SOURCE_FINGERPRINT" > "$SOURCE_STAMP"
    echo "PPT GOD 已启动：${BASE_URL}"
    open_pptgod
    echo
    echo "启动页会自动检查模型能力；无需登录，可以直接进入工作台。"
    echo "可以关闭这个窗口，服务会继续在 Docker 里运行。"
    exit 0
  fi
  sleep 1
done

echo "服务没有在预期时间内启动成功。下面是最近的后端日志："
echo
docker compose logs --tail=80 backend || true
echo
read -r -p "按回车关闭窗口。"
