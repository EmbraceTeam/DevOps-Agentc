#!/usr/bin/env bash
# opsctl-plugin 每日更新检查 — 供系统 crontab 调用.
#
# 逻辑: git fetch 对比本地/远程 HEAD, 有更新才执行
#   hermes plugins update + 逐 profile gateway 重启; 无更新则静默退出.
# 日志: ~/.hermes/logs/opsctl-update.log
#
# crontab 示例 (以实际部署用户为例):
#   0 8 * * * $HOME/.hermes/plugins/opsctl-plugin/bin/opsctl-update.sh

set -euo pipefail

# cron 环境 PATH 精简, 补齐 hermes 可执行目录
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

PLUGIN_DIR="$HOME/.hermes/plugins/opsctl-plugin"
PLUGIN_NAME="opsctl-plugin"
LOG_FILE="$HOME/.hermes/logs/opsctl-update.log"

log() { echo "$(date '+%F %T') $*" >> "$LOG_FILE"; }

if [ ! -d "$PLUGIN_DIR/.git" ]; then
    log "插件目录不存在, 跳过: $PLUGIN_DIR"
    exit 0
fi

cd "$PLUGIN_DIR"
if ! git fetch origin master 2>/dev/null; then
    log "git fetch 失败, 跳过本次"
    exit 1
fi
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0   # 无更新, 静默
fi

log "检测到更新: ${LOCAL:0:7} -> ${REMOTE:0:7}"
if ! hermes plugins update "$PLUGIN_NAME" >> "$LOG_FILE" 2>&1; then
    log "hermes plugins update 失败"
    exit 1
fi

hermes gateway restart >> "$LOG_FILE" 2>&1 || log "[WARN] 默认 profile 重启失败"
for p in "$HOME/.hermes/profiles"/*/; do
    [ -d "$p" ] || continue
    name=$(basename "$p")
    hermes --profile "$name" gateway restart >> "$LOG_FILE" 2>&1 \
        || log "[WARN] profile $name 重启失败"
done

log "更新完成"
