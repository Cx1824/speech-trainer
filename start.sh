#!/bin/bash
# 一键启动 speech-trainer 前后端（自带崩溃自愈）
#
# 用法:
#   ./start.sh           前台运行（Ctrl+C 全部停止）
#   ./start.sh daemon    后台运行，进程崩溃自动重启，关终端不影响
#   ./start.sh stop      全部停止
#   ./start.sh restart   重启
#   ./start.sh status    查看状态
#   ./start.sh install   检查可用的 launchd 代理（本仓库未内置 plist）
#   ./start.sh uninstall 卸载已存在的 launchd 代理
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
RUN_DIR="/tmp/speech-trainer-$(id -u)-$(printf '%s' "$ROOT" | cksum | awk '{print $1}')"
mkdir -p "$RUN_DIR"
BLOG="$RUN_DIR/backend.log"
FLOG="$RUN_DIR/frontend.log"
BPID="$RUN_DIR/backend.supervisor.pid"
FPID="$RUN_DIR/frontend.supervisor.pid"

process_is_ours() {
  local pid="$1" dir="$2" name="$3" command
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$pid" -gt 1 ] || return 1
  command="$(ps -p "$pid" -o command= 2>/dev/null)"
  case "$command" in
    *"NAME=\"$name\"; DIR=\"$dir\""*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_supervisor() {
  local pid_file="$1" dir="$2" name="$3" pid
  [ -f "$pid_file" ] || return 0
  pid="$(sed -n '1p' "$pid_file" 2>/dev/null)"
  if process_is_ours "$pid" "$dir" "$name"; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
  else
    echo "跳过未验证的 $name PID：${pid:-unknown}" >&2
  fi
  rm -f "$pid_file"
}

stop_all() {
  # 仅停止本项目记录且通过命令行身份校验的 supervisor。
  # 不按端口清理，避免误杀其他项目占用同一端口的进程。
  stop_supervisor "$BPID" "$BACKEND" backend
  stop_supervisor "$FPID" "$FRONTEND" frontend
}

port_is_available() {
  local port="$1" python_bin
  if [ -x "$BACKEND/.venv/bin/python" ]; then
    python_bin="$BACKEND/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  else
    echo "无法检查端口 $port：未找到 Python 3。" >&2
    return 2
  fi
  "$python_bin" - "$port" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

ensure_ports_available() {
  local failed=0
  if ! port_is_available "$BACKEND_PORT"; then
    echo "后端端口 $BACKEND_PORT 已被其他进程占用；未启动，也不会终止该进程。" >&2
    failed=1
  fi
  if ! port_is_available "$FRONTEND_PORT"; then
    echo "前端端口 $FRONTEND_PORT 已被其他进程占用；未启动，也不会终止该进程。" >&2
    failed=1
  fi
  [ "$failed" -eq 0 ]
}

# supervisor：循环启动进程，崩溃后 2 秒自动重启
supervise() {
  local name="$1" dir="$2" cmd="$3" logf="$4" pidf="$5"
  nohup bash -c '
    NAME="'"$name"'"; DIR="'"$dir"'"; CMD="'"$cmd"'"; LOG="'"$logf"'"
    cd "$DIR" || exit 1
    CHILD_PID=""
    stop_child() {
      if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill "$CHILD_PID" 2>/dev/null || true
        wait "$CHILD_PID" 2>/dev/null || true
      fi
    }
    trap "stop_child; exit 143" INT TERM
    trap "stop_child" EXIT
    while true; do
      echo "[$(date "+%m-%d %H:%M:%S")] [$NAME] 启动中..." >> "$LOG"
      bash -c "exec $CMD" >> "$LOG" 2>&1 &
      CHILD_PID=$!
      wait "$CHILD_PID"
      code=$?
      CHILD_PID=""
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
    ensure_ports_available || exit 1
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
    if [ "$(uname -s)" != "Darwin" ]; then
      echo "当前系统不是 macOS；launchd 代理未安装，请使用 ./start.sh daemon。"
      exit 0
    fi
    echo "本仓库未内置 launchd plist，未执行安装；请使用 ./start.sh daemon。"
    exit 0
    ;;
  uninstall)
    if [ "$(uname -s)" != "Darwin" ]; then
      echo "当前系统不是 macOS；没有需要卸载的 launchd 代理。"
      exit 0
    fi
    found=0
    failed=0
    for plist in "$HOME/Library/LaunchAgents/com.speechtrainer.backend.plist" "$HOME/Library/LaunchAgents/com.speechtrainer.frontend.plist"; do
      if [ -f "$plist" ]; then
        if ! launchctl unload "$plist" 2>&1; then
          failed=1
        fi
        found=1
      fi
    done
    if [ "$found" -eq 0 ]; then
      echo "未找到 speechtrainer launchd plist，没有需要卸载的代理。"
    elif [ "$failed" -ne 0 ]; then
      echo "已找到 launchd plist，但部分代理未能卸载；请在对应用户会话中重试。" >&2
      exit 1
    else
      echo "已卸载存在的 launchd 代理。"
    fi
    ;;
  *)
    stop_all
    ensure_ports_available || exit 1
    trap 'stop_all; exit 0' INT TERM
    start_backend
    start_frontend
    check
    echo ""
    echo "打开 http://localhost:$FRONTEND_PORT （Ctrl+C 停止全部）"
    wait
    ;;
esac
