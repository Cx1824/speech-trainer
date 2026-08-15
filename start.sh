#!/bin/bash
# 一键启动 speech-trainer 前后端（自带崩溃自愈）
#
# 用法:
#   ./start.sh           前台运行（Ctrl+C 全部停止）
#   ./start.sh daemon    后台运行，进程崩溃自动重启，关终端不影响
#   ./start.sh stop      全部停止
#   ./start.sh restart   重启
#   ./start.sh status    查看状态
#   ./start.sh install   安装 launchd 代理（开机/登录自启 + 崩溃自愈，需在自己终端跑）
#   ./start.sh uninstall 卸载 launchd 代理
#
# 说明: daemon 模式下每个服务由一个 bash supervisor 包着，
#       子进程崩溃后 2 秒自动重启（nohup + disown，关终端也存活）。
#       要连「注销/重启」都自动拉起，请用 launchd：见文末说明或
#       ./start.sh install 安装（如环境支持）。

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5178
BLOG=/tmp/st-backend.log
FLOG=/tmp/st-frontend.log
BPID=/tmp/st-backend.pid
FPID=/tmp/st-frontend.pid

stop_all() {
  # 先停 supervisor（pid 文件）
  for f in "$BPID" "$FPID"; do
    if [ -f "$f" ]; then
      kill "$(cat "$f")" 2>/dev/null
      rm -f "$f"
    fi
  done
  # 再按端口兜底清残留（含 supervisor 的子进程）
  lsof -ti :$BACKEND_PORT 2>/dev/null | xargs kill 2>/dev/null
  lsof -ti :$FRONTEND_PORT 2>/dev/null | xargs kill 2>/dev/null
  sleep 1
}

# supervisor：循环启动进程，崩溃后 2 秒自动重启
supervise() {
  local name="$1" dir="$2" cmd="$3" logf="$4" pidf="$5"
  nohup bash -c '
    NAME="'"$name"'"; DIR="'"$dir"'"; CMD="'"$cmd"'"; LOG="'"$logf"'"
    cd "$DIR" || exit 1
    trap "echo \"[$(date "+%m-%d %H:%M:%S")] [$NAME] supervisor 停止\" >> \"$LOG\"; kill 0 2>/dev/null" EXIT
    while true; do
      echo "[$(date "+%m-%d %H:%M:%S")] [$NAME] 启动中..." >> "$LOG"
      eval "$CMD" >> "$LOG" 2>&1
      code=$?
      echo "[$(date "+%m-%d %H:%M:%S")] [$NAME] 退出($code)，2 秒后重启..." >> "$LOG"
      sleep 2
    done
  ' < /dev/null &
  echo $! > "$pidf"
  disown 2>/dev/null
}

check() {
  sleep 3
  B=$(curl -s -m 3 --noproxy '*' -o /dev/null -w "%{http_code}" http://127.0.0.1:$BACKEND_PORT/api/health)
  F=$(curl -s -m 3 --noproxy '*' -o /dev/null -w "%{http_code}" http://127.0.0.1:$FRONTEND_PORT/)
  echo "后端  :$BACKEND_PORT → ${B:-down}"
  echo "前端  :$FRONTEND_PORT → ${F:-down}"
}

start_backend() {
  cd "$BACKEND" || exit 1
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT &
  BACKEND_PID=$!
}

start_frontend() {
  cd "$FRONTEND" || exit 1
  node_modules/.bin/vite --port $FRONTEND_PORT --host 127.0.0.1 &
  FRONTEND_PID=$!
}

case "${1:-}" in
  daemon)
    stop_all
    supervise backend  "$BACKEND"  ".venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT"  "$BLOG" "$BPID"
    supervise frontend "$FRONTEND" "node_modules/.bin/vite --port $FRONTEND_PORT --host 127.0.0.1"          "$FLOG" "$FPID"
    check
    echo ""
    echo "后台运行中（崩溃自动重启，关终端不影响）。"
    echo "日志: $BLOG / $FLOG"
    echo "停止: ./start.sh stop"
    ;;
  stop)
    stop_all
    echo "已全部停止"
    ;;
  restart)
    stop_all
    "$0" daemon
    ;;
  status)
    B=$(curl -s -m 3 --noproxy '*' -o /dev/null -w "%{http_code}" http://127.0.0.1:$BACKEND_PORT/api/health)
    F=$(curl -s -m 3 --noproxy '*' -o /dev/null -w "%{http_code}" http://127.0.0.1:$FRONTEND_PORT/)
    echo "后端  :$BACKEND_PORT → ${B:-down}"
    echo "前端  :$FRONTEND_PORT → ${F:-down}"
    [ -f "$BPID" ] && echo "backend supervisor pid : $(cat "$BPID")"
    [ -f "$FPID" ] && echo "frontend supervisor pid: $(cat "$FPID")"
    ;;
  install)
    launchctl load "$HOME/Library/LaunchAgents/com.speechtrainer.backend.plist" 2>&1
    launchctl load "$HOME/Library/LaunchAgents/com.speechtrainer.frontend.plist" 2>&1
    echo "已安装 launchd 代理：开机/登录自动启动，崩溃自动重启。"
    echo "查看: launchctl list | grep speechtrainer"
    echo "卸载: ./start.sh uninstall"
    ;;
  uninstall)
    launchctl unload "$HOME/Library/LaunchAgents/com.speechtrainer.backend.plist" 2>&1
    launchctl unload "$HOME/Library/LaunchAgents/com.speechtrainer.frontend.plist" 2>&1
    echo "已卸载 launchd 代理。"
    ;;
  *)
    stop_all
    trap 'stop_all; exit 0' INT TERM
    start_backend
    start_frontend
    check
    echo ""
    echo "打开 http://localhost:$FRONTEND_PORT （Ctrl+C 停止全部）"
    wait
    ;;
esac
