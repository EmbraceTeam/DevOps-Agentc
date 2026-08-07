#!/usr/bin/env bash
# opsctl Hermes 目录插件部署脚本
#
# 一次完成: 安装插件 (默认 profile) → 其他 profile symlink 共享 → 逐 profile 启用 → 重启。
# 更新只需 hermes plugins update opsctl-plugin (symlink 自动跟随)。
#
# 用法:
#   bash deploy.sh                                        # 本地执行 (交互询问 profile)
#   bash deploy.sh --profiles "ops eog"                   # 本地执行, 指定 profile
#   bash deploy.sh --remote user@host                     # 远程执行 (交互询问 profile)
#   bash deploy.sh --remote user@host --profiles "ops eog"   # 远程执行, 指定 profile

set -euo pipefail

GIT_URL="https://github.com/<your-org>/DevOps-Agent.git"
PLUGIN_NAME="opsctl-plugin"

info()  { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

REMOTE=""
PROFILES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --remote) REMOTE="$2"; shift 2 ;;
        --remote=*) REMOTE="${1#*=}"; shift ;;
        --profiles) PROFILES="$2"; shift 2 ;;
        --profiles=*) PROFILES="${1#*=}"; shift ;;
        -h|--help)
            sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^$' || true
            exit 0
            ;;
        *) error "未知参数: $1";;
    esac
done

# 交互询问 profile 列表 (空格分隔)
if [ -z "$PROFILES" ]; then
    read -rp "其他需要启用插件的 profile 列表 (空格分隔, 可留空): " PROFILES
fi

# 生成部署命令序列 (本地执行或经 ssh 远程执行)
gen_cmds() {
    cat << EOF
set -euo pipefail

info()  { echo "[INFO] \$*"; }
error() { echo "[ERROR] \$*" >&2; exit 1; }

PLUGIN_NAME="$PLUGIN_NAME"
GIT_URL="$GIT_URL"
PROFILES="$PROFILES"
DEFAULT_PLUGINS_DIR="\$HOME/.hermes/plugins"

echo '=== 1/5 安装插件 (默认 profile) ==='
hermes plugins install "\$GIT_URL"

echo '=== 2/5 其他 profile symlink 共享 ==='
for p in \$PROFILES; do
    [ -z "\$p" ] && continue
    target="\$HOME/.hermes/profiles/\$p/plugins"
    link="\$target/\$PLUGIN_NAME"
    mkdir -p "\$target"
    if [ -e "\$link" ] && [ ! -L "\$link" ]; then
        echo "[WARN] \$link 已存在且不是 symlink, 跳过 (请手动处理)"
        continue
    fi
    ln -sf "\$DEFAULT_PLUGINS_DIR/\$PLUGIN_NAME" "\$link"
    echo "  ✓ profile \$p → symlink 已建立"
done

echo '=== 3/5 技能树 symlink (agent 可见) ==='
# 插件注册的 skill 不进 <available_skills> 索引 (Hermes 设计),
# 需链接到各 HERMES_HOME 的 skills 树 agent 才能看到.
SKILL_SRC="\$DEFAULT_PLUGINS_DIR/\$PLUGIN_NAME/src/opsctl/plugin/skills/ops-inspect"
link_skill() {
    local home_dir="\$1"
    local skill_dir="\$home_dir/skills/devops"
    local skill_link="\$skill_dir/ops-inspect"
    mkdir -p "\$skill_dir"
    if [ -e "\$skill_link" ] && [ ! -L "\$skill_link" ]; then
        echo "[WARN] \$skill_link 已存在且不是 symlink, 跳过"
        return
    fi
    ln -sfn "\$SKILL_SRC" "\$skill_link"
    echo "  ✓ skill ops-inspect → \$home_dir/skills/devops/"
}
link_skill "\$HOME/.hermes"
for p in \$PROFILES; do
    [ -z "\$p" ] && continue
    link_skill "\$HOME/.hermes/profiles/\$p"
done

echo '=== 4/5 逐 profile 启用插件 ==='
hermes plugins enable "\$PLUGIN_NAME"
for p in \$PROFILES; do
    [ -z "\$p" ] && continue
    hermes --profile "\$p" plugins enable "\$PLUGIN_NAME" \
        || echo "[WARN] profile \$p 启用失败"
done

echo '=== 5/5 重启 Hermes ==='
hermes gateway restart || echo "[WARN] 默认 profile 重启失败"
for p in \$PROFILES; do
    [ -z "\$p" ] && continue
    hermes --profile "\$p" gateway restart \
        || echo "[WARN] profile \$p 重启失败"
done

echo ''
echo '⚠️  首次部署还需在 Hermes venv 安装 CLI 依赖 (一次性):'
echo '    \$HERMES_VENV/bin/pip install typer rich'
echo ''
echo '✅ 部署完成! 验证: hermes plugins list'
EOF
}

if [ -n "$REMOTE" ]; then
    info "在 $REMOTE 上执行部署..."
    ssh "$REMOTE" bash -s -- <<< "$(gen_cmds)"
    info "远程部署完成"
else
    bash -s -- <<< "$(gen_cmds)"
fi
