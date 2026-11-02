#!/usr/bin/env bash
# ============================================================
# 启动脚本：香港服务器直连版（无需代理）
# 用途: 后台同时启动
#   - 数据抓取 (main.py)
#   - 策略看板 (gunicorn 启动 dashboard_final:app , 监听 8002)
# 输出: 运行日志分别写入 logs/ 目录
# ============================================================

set -e

# 切换到脚本所在目录（保证无论在哪执行都能找到 CSV 文件）
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

mkdir -p "${APP_DIR}/logs"

# ---------------- 配置 ----------------
PY_BIN="${PY_BIN:-python3}"          # 若用虚拟环境，改成 /path/to/venv/bin/python
DASH_PORT="${DASH_PORT:-8002}"       # 看板端口，外部用 Nginx 反代到 8002
WORKERS="${WORKERS:-3}"              # gunicorn workers

PID_MAIN="${APP_DIR}/logs/main.pid"
PID_DASH="${APP_DIR}/logs/dashboard.pid"
LOG_MAIN="${APP_DIR}/logs/main.log"
LOG_DASH="${APP_DIR}/logs/dashboard.log"

# ---------------- 停止之前的进程 ----------------
stop_safe() {
    local pidfile="$1"
    local name="$2"
    if [ -f "$pidfile" ]; then
        OLD_PID="$(cat "$pidfile" 2>/dev/null || true)"
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            echo "🧹 停止已存在的 $name (PID=$OLD_PID) ..."
            kill "$OLD_PID" 2>/dev/null || true
            # 最多等 10 秒让它优雅退出，否则强杀
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                kill -0 "$OLD_PID" 2>/dev/null || break
                sleep 1
            done
            kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
}

stop_safe "$PID_MAIN" "数据抓取 main.py"
stop_safe "$PID_DASH" "看板 gunicorn"

# 保险：清理可能残留的 gunicorn 进程（同名组）
pkill -f "gunicorn.*dashboard_final:app" 2>/dev/null || true
sleep 1

# ---------------- 启动 main.py ----------------
echo "🚀 启动数据抓取 main.py ..."
nohup "${PY_BIN}" -u "${APP_DIR}/main.py" > "$LOG_MAIN" 2>&1 &
echo $! > "$PID_MAIN"
echo "   PID=$(cat "$PID_MAIN")  | 日志: $LOG_MAIN"

# ---------------- 启动 dashboard (gunicorn) ----------------
echo "📊 启动策略看板 gunicorn :$DASH_PORT ..."
cd "$APP_DIR"
nohup "${PY_BIN}" -m gunicorn \
    --workers "$WORKERS" \
    --bind "0.0.0.0:${DASH_PORT}" \
    --access-logfile "${APP_DIR}/logs/dashboard_access.log" \
    --error-logfile "${APP_DIR}/logs/dashboard_error.log" \
    --timeout 60 \
    dashboard_final:app > "$LOG_DASH" 2>&1 &
echo $! > "$PID_DASH"
echo "   PID=$(cat "$PID_DASH")  | 日志: $LOG_DASH"

# ---------------- 状态 ----------------
sleep 2
echo ""
echo "=================================================="
echo "✅ 所有服务已在后台启动（香港直连模式，不走代理）"
echo "1. 数据抓取 (main.py)       : 运行中"
echo "2. 策略看板 (Gunicorn)       : 0.0.0.0:$DASH_PORT"
echo ""
echo "👉 常用命令:"
echo "   查看抓取日志 : tail -f logs/main.log"
echo "   查看看板日志 : tail -f logs/dashboard_error.log"
echo "   全部停止     : bash $(basename "$0") stop"
echo "   运行状态     : bash $(basename "$0") status"
echo ""
echo "👉 外网访问: 记得在云服务器安全组/防火墙放行 $DASH_PORT 端口"
echo "   推荐用 Nginx / NPM 反代到 127.0.0.1:$DASH_PORT"
echo "=================================================="

# ---------------- 子命令: stop / status ----------------
cmd="$1"
case "$cmd" in
    stop)
        stop_safe "$PID_MAIN" "数据抓取 main.py"
        stop_safe "$PID_DASH" "看板 gunicorn"
        echo "🛑 所有服务已停止。"
        exit 0
        ;;
    status)
        echo "📋 运行状态:"
        for pair in "数据抓取|$PID_MAIN" "策略看板|$PID_DASH"; do
            name="${pair%%|*}"
            pfile="${pair##*|}"
            if [ -f "$pfile" ]; then
                pid="$(cat "$pfile")"
                if kill -0 "$pid" 2>/dev/null; then
                    echo "   ✅ $name 运行中 (PID=$pid)"
                else
                    echo "   ❌ $name 进程不存在 (PID文件: $pid)"
                fi
            else
                echo "   ⚪  $name 未启动 (PID文件不存在)"
            fi
        done
        # 端口检查
        (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk -v p=":$DASH_PORT" '$4 ~ p {print "   🔌 端口 "p" 已监听"; found=1} END{if(!found) print "   🔌 端口 "p" 未监听"}'
        exit 0
        ;;
esac
