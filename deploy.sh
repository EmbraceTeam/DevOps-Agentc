#!/usr/bin/env bash
# opsctl Hermes Plugin 一键部署脚本
# 用法:
#   bash deploy.sh                         # 交互式
#   bash deploy.sh --local                 # 直接在服务器上安装
#   bash deploy.sh --soul /path/to/SOUL.md # 指定 SOUL.md 路径

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEEL_DIR="$PROJECT_DIR/dist"

info()  { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ---- 解析参数 ----
SOUL_PATH=""
LOCAL=false
while [ $# -gt 0 ]; do
    case "$1" in
        --local) LOCAL=true; shift ;;
        --soul) SOUL_PATH="$2"; shift 2 ;;
        --soul=*) SOUL_PATH="${1#*=}"; shift ;;
        *) error "未知参数: $1";;
    esac
done

# ---- 检测包管理器 ----
if command -v uv &>/dev/null; then
    PKG="uv"
elif command -v pip &>/dev/null; then
    PKG="pip"
else
    error "未找到 pip 或 uv"
fi

# ---- 本地打包 ----
info "打包 opsctl wheel..."
cd "$PROJECT_DIR"
if [ "$PKG" = "uv" ]; then
    uv build --wheel 2>&1 | tail -1 || uv pip install build 2>/dev/null && uv build --wheel
else
    pip install build 2>/dev/null || true
    python -m build --wheel
fi
WHEEL_FILE=$(find "$WHEEL_DIR" -maxdepth 1 -name 'opsctl-*.whl' | head -1)
if [ -z "$WHEEL_FILE" ]; then
    error "打包失败: 未生成 wheel 文件"
fi
info "生成: $WHEEL_FILE"

# ---- 远程部署模式 ----
if [ "$LOCAL" = false ]; then
    read -rp "Hermes 服务器地址 (user@host, 留空只在本机安装): " REMOTE
    if [ -n "$REMOTE" ]; then
        if [ -z "$SOUL_PATH" ]; then
            read -rp "SOUL.md 路径 (留空则跳过, 如 /home/<user>/.hermes/profiles/operation): " SOUL_PATH
        fi
        info "上传到 $REMOTE:~/"
        UPLOAD_FILES=("$WHEEL_FILE")
        if [ -n "$SOUL_PATH" ]; then
            UPLOAD_FILES+=("$PROJECT_DIR/src/opsctl/plugin/contexts/ops-engineer/CONTEXT.md")
        fi
        scp "${UPLOAD_FILES[@]}" "$REMOTE":~/
        REMOTE_CMD="set -euo pipefail
# 安装 opsctl
if command -v uv &>/dev/null; then
    uv pip install ~/opsctl-*.whl
else
    pip install ~/opsctl-*.whl
fi
# 启用 Hermes Plugin
mkdir -p ~/.hermes
if ! grep -q 'opsctl' ~/.hermes/config.yaml 2>/dev/null; then
    cat >> ~/.hermes/config.yaml << 'CONFIG'
plugins:
  enabled: [opsctl]
CONFIG
fi
"
        if [ -n "$SOUL_PATH" ]; then
            REMOTE_CMD="$REMOTE_CMD
# 安装角色提示词
mkdir -p \"\$(dirname \"$SOUL_PATH\")\"
cp ~/CONTEXT.md \"$SOUL_PATH\"
echo \"SOUL.md 已安装到: $SOUL_PATH\"
"
        fi
        REMOTE_CMD="$REMOTE_CMD
# 验证
echo '=== 验证 ==='
opsctl resource types
echo ''
echo '✅ 部署完成！重启 Hermes 后 Plugin 生效。'
echo \"如需启用 Plugin, 请确认 ~/.hermes/config.yaml 包含:
plugins:
  enabled: [opsctl]\"
"
        ssh "$REMOTE" bash -s -- <<< "$REMOTE_CMD"
        info "远程部署完成"
        exit 0
    fi
fi

# ---- 本地安装（--local 或无远程）----
if [ "$PKG" = "uv" ]; then
    uv pip install "$WHEEL_FILE"
else
    pip install "$WHEEL_FILE"
fi
mkdir -p ~/.hermes
if ! grep -q "opsctl" ~/.hermes/config.yaml 2>/dev/null; then
    cat >> ~/.hermes/config.yaml << 'CONFIG'
plugins:
  enabled: [opsctl]
CONFIG
fi
if [ -n "$SOUL_PATH" ]; then
    mkdir -p "$(dirname "$SOUL_PATH")"
    cp "$PROJECT_DIR/src/opsctl/plugin/contexts/ops-engineer/CONTEXT.md" "$SOUL_PATH"
    echo "SOUL.md 已安装到: $SOUL_PATH"
fi
echo "=== 验证 ==="
opsctl resource types
echo ""
echo "✅ 部署完成！重启 Hermes 后 Plugin 生效。"
