#!/usr/bin/env bash
# opsctl Hermes Plugin 一键部署脚本
# 用法:
#   在本机:  bash deploy.sh                  # 打包 + 传服务器 + 安装
#   在服务器: bash deploy.sh --local          # 直接在服务器上安装

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEEL_DIR="$PROJECT_DIR/dist"

info()  { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ---- 本地打包 ----
info "打包 opsctl wheel..."
cd "$PROJECT_DIR"
if ! command -v build &>/dev/null; then
    pip install build
fi
python -m build --wheel
WHEEL_FILE=$(find "$WHEEL_DIR" -maxdepth 1 -name 'opsctl-*.whl' | head -1)
info "生成: $WHEEL_FILE"

# ---- 远程部署模式 ----
if [ $# -eq 0 ]; then
    read -rp "Hermes 服务器地址 (user@host, 留空只在本机安装): " REMOTE
    if [ -n "$REMOTE" ]; then
        info "上传到 $REMOTE:~/"
        scp "$WHEEL_FILE" "$PROJECT_DIR/src/opsctl/plugin/contexts/ops-engineer/CONTEXT.md" "$REMOTE":~/
        ssh "$REMOTE" bash -s -- --local << 'REMOTE_SCRIPT'
set -euo pipefail
# ----- 服务器端安装 -----
pip install ~/opsctl-*.whl
mkdir -p ~/.hermes
# 仅在未启用时追加
if ! grep -q "opsctl" ~/.hermes/config.yaml 2>/dev/null; then
    cat >> ~/.hermes/config.yaml << 'CONFIG'
plugins:
  enabled: [opsctl]
CONFIG
fi
# 安装角色提示词
cp ~/CONTEXT.md ~/.hermes/SOUL.md 2>/dev/null || true
# 验证
echo "=== 验证 ==="
opsctl resource types
echo ""
echo "✅ 部署完成！重启 Hermes 后 Plugin 生效。"
REMOTE_SCRIPT
        info "远程部署完成"
        exit 0
    fi
fi

# ---- 本地安装（--local 或未指定远程）----
pip install "$WHEEL_FILE"
mkdir -p ~/.hermes
if ! grep -q "opsctl" ~/.hermes/config.yaml 2>/dev/null; then
    cat >> ~/.hermes/config.yaml << 'CONFIG'
plugins:
  enabled: [opsctl]
CONFIG
fi
cp "$PROJECT_DIR/src/opsctl/plugin/contexts/ops-engineer/CONTEXT.md" ~/.hermes/SOUL.md 2>/dev/null || true
echo "=== 验证 ==="
opsctl resource types
echo ""
echo "✅ 部署完成！重启 Hermes 后 Plugin 生效。"
