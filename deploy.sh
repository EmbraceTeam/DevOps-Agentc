#!/usr/bin/env bash
# opsctl Hermes 目录插件部署引导脚本
#
# 新部署方式: 插件以目录插件形态从 Git 仓库分发 (方案 A′),
# hermes plugins install/update 一条命令管理插件 + CLI, 无需打包 wheel.
#
# 用法:
#   bash deploy.sh              # 打印部署指引
#   bash deploy.sh --remote user@host   # 在远程服务器上执行部署命令

set -euo pipefail

GIT_URL="https://github.com/<your-org>/DevOps-Agent.git"

info()  { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

REMOTE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --remote) REMOTE="$2"; shift 2 ;;
        --remote=*) REMOTE="${1#*=}"; shift ;;
        -h|--help) sed -n '1,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) error "未知参数: $1";;
    esac
done

REMOTE_CMDS="set -euo pipefail
echo '=== 1/4 安装插件 (克隆到 ~/.hermes/plugins/opsctl-plugin/) ==='
hermes plugins install $GIT_URL
echo ''
echo '=== 2/4 一次性安装 CLI 依赖 (Hermes venv) ==='
echo '请确认 HERMES_VENV 路径后执行:'
echo '  \$HERMES_VENV/bin/pip install typer rich'
echo ''
echo '=== 3/4 启用插件 ==='
hermes plugins enable opsctl-plugin
echo ''
echo '=== 4/4 重启 Hermes ==='
hermes gateway restart
echo ''
echo '✅ 部署完成! 验证: hermes plugins list'
"

if [ -n "$REMOTE" ]; then
    info "在 $REMOTE 上执行部署命令..."
    ssh "$REMOTE" bash -s -- <<< "$REMOTE_CMDS"
    info "远程部署完成"
else
    cat << 'GUIDE'
opsctl Hermes 目录插件部署指引
================================

在 Hermes 服务器上执行以下命令:

  1. 安装插件 (克隆到 ~/.hermes/plugins/opsctl-plugin/)
     hermes plugins install https://github.com/<your-org>/DevOps-Agent.git

  2. 一次性安装 CLI 依赖 (Hermes venv)
     $HERMES_VENV/bin/pip install typer rich

  3. 启用插件
     hermes plugins enable opsctl-plugin

  4. 重启 Hermes
     hermes gateway restart

更新:  hermes plugins update opsctl-plugin
卸载:  hermes plugins remove opsctl-plugin

详细说明见 DEPLOY.md。也可用 --remote user@host 在远程服务器上直接执行。
GUIDE
fi
