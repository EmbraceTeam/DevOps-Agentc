#!/usr/bin/env bash
# install.sh — 一键安装 Hermes Agent + opsctl-plugin
#
# 流程:
#   1. 预检 (git/curl/OS)
#   2. 安装 Hermes (官方 curl 安装器, 已装则跳过)
#   3. 把 typer/rich 装进 Hermes venv (opsctl CLI 运行时依赖)
#   4. 安装本仓库为 Hermes 插件 (hermes plugins install --enable)
#   5. 验证
#
# 用法:
#   ./install.sh                             # 全流程, 使用默认 Git URL
#   ./install.sh --git-url <url>             # 指定插件源仓库
#   ./install.sh --skip-hermes               # 跳过 Hermes 安装 (本机已装)
#   ./install.sh --no-restart                # 装完不重启 gateway
#   ./install.sh --hermes-install-args="--skip-browser"  # 透传给 Hermes 安装器
#   ./install.sh --uninstall                 # 卸载插件 (不卸 Hermes)
#
# 幂等: 重复执行安全; 已装步骤自动跳过.
set -euo pipefail

# ============================================================================
# 默认配置
# ============================================================================
DEFAULT_GIT_URL="https://github.com/EmbraceTeam/DevOps-Agentc.git"
PLUGIN_NAME="opsctl-plugin"

# 可被参数覆盖
GIT_URL="$DEFAULT_GIT_URL"
SKIP_HERMES=false
SKIP_LLM=false
NO_RESTART=false
UNINSTALL=false
HERMES_INSTALL_ARGS=""
LLM_PROVIDER=""    # 预选 provider (跳过菜单, 但仍交互输入 key)

# provider → (env_var, default_model, 需要key?) 映射
# olama 无 key, 用占位; 其余都是真实 API Key 变量名
declare -A PROVIDER_ENV=(
    [anthropic]="ANTHROPIC_API_KEY"
    [openai]="OPENAI_API_KEY"
    [openrouter]="OPENROUTER_API_KEY"
    [gemini]="GEMINI_API_KEY"
    [ollama]="OLLAMA_API_KEY"
    [nous]="NOUS_API_KEY"
    [custom]="OPENAI_API_KEY"
)
declare -A PROVIDER_MODEL=(
    [anthropic]="anthropic/claude-sonnet-5"
    [openai]="openai/gpt-5"
    [openrouter]="openrouter/anthropic/claude-sonnet-5"
    [gemini]="gemini/gemini-3-flash"
    [ollama]="ollama/llama3.2"
    [nous]="nous/hermes-4"
    [custom]=""
)
declare -A PROVIDER_NEEDS_KEY=(
    [anthropic]=1 [openai]=1 [openrouter]=1 [gemini]=1 [nous]=1 [custom]=1
    [ollama]=0
)

# ============================================================================
# 颜色输出 (非 TTY 时自动降级)
# ============================================================================
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_BLUE=$'\033[34m'
    C_DIM=$'\033[2m'
else
    C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_DIM=""
fi

log_info()  { printf '%s▸%s %s\n'  "${C_BLUE}"   "${C_RESET}" "$*"; }
log_ok()    { printf '%s✓%s %s\n'  "${C_GREEN}"  "${C_RESET}" "$*"; }
log_warn()  { printf '%s!%s %s\n'  "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_error() { printf '%s✗%s %s\n'  "${C_RED}"    "${C_RESET}" "$*" >&2; }
log_step()  { printf '\n%s══ %s ══%s\n' "${C_BOLD}${C_BLUE}" "$*" "${C_RESET}"; }

die() { log_error "$*"; exit 1; }

# ============================================================================
# 参数解析
# ============================================================================
usage() {
    cat <<'EOF'
install.sh — 一键安装 Hermes Agent + opsctl-plugin

用法:
  ./install.sh [OPTIONS]

选项:
  --git-url <url>              插件源 Git URL (默认: EmbraceTeam/DevOps-Agentc)
  --skip-hermes                跳过 Hermes 安装 (本机已装时用)
  --skip-llm                   跳过 LLM 配置阶段 (之后手动跑 hermes setup)
  --provider <name>            预选 LLM provider, 跳过选择菜单 (仍会提示输入 key)
                               可选: anthropic, openai, openrouter, gemini, ollama, nous, custom
  --no-restart                 装完不执行 hermes gateway restart
  --uninstall                  卸载插件 (保留 Hermes)
  --hermes-install-args="..."  透传给 Hermes 官方安装器 (如 --skip-browser)
  -h, --help                   显示此帮助

环境变量:
  HERMES_HOME                  Hermes 数据目录 (默认 ~/.hermes)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --git-url)
            GIT_URL="$2"; shift 2 ;;
        --git-url=*)
            GIT_URL="${1#*=}"; shift ;;
        --skip-hermes)
            SKIP_HERMES=true; shift ;;
        --skip-llm)
            SKIP_LLM=true; shift ;;
        --provider)
            LLM_PROVIDER="$2"; shift 2 ;;
        --provider=*)
            LLM_PROVIDER="${1#*=}"; shift ;;
        --no-restart)
            NO_RESTART=true; shift ;;
        --uninstall)
            UNINSTALL=true; shift ;;
        --hermes-install-args)
            HERMES_INSTALL_ARGS="$2"; shift 2 ;;
        --hermes-install-args=*)
            HERMES_INSTALL_ARGS="${1#*=}"; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            die "未知参数: $1 (用 --help 查看用法)" ;;
    esac
done

# ============================================================================
# 路径解析
# ============================================================================
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_BIN_DIR="$HERMES_HOME/bin"
HERMES_REPO_DIR="$HERMES_HOME/hermes-agent"
HERMES_VENV_DIR="$HERMES_REPO_DIR/venv"

# ============================================================================
# 工具函数
# ============================================================================

# 在常见位置找 hermes 可执行文件 (刚装完时当前 shell 的 PATH 可能还没更新)
resolve_hermes_bin() {
    local cmd
    # 1. PATH 中
    if cmd=$(command -v hermes 2>/dev/null); then
        printf '%s' "$cmd"; return 0
    fi
    # 2. Hermes 管理的 bin 目录
    local candidate="$HERMES_BIN_DIR/hermes"
    [[ -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    # 3. 仓库目录下的 venv 入口 (curl 安装器生成的 launcher 形态)
    candidate="$HERMES_REPO_DIR/hermes"
    [[ -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    return 1
}

# 在常见位置找 Hermes venv 的 python (用来装 typer/rich)
resolve_hermes_venv_python() {
    local p hermes_bin
    # 1. 最权威: 从 hermes launcher 提取 venv python (hermes 实际用的解释器)
    #    launcher 形如: exec "/usr/local/lib/hermes-agent/venv/bin/python" "..."
    if hermes_bin=$(resolve_hermes_bin); then
        p=$(grep -oE '/[^" ]*/venv/bin/python' "$hermes_bin" 2>/dev/null | head -1)
        if [[ -n "$p" && -x "$p" ]]; then
            printf '%s' "$p"; return 0
        fi
    fi
    # 2. 官方安装器系统布局 (root): /usr/local/lib/hermes-agent/venv
    # 3. 官方安装器用户布局: ~/.local/lib/hermes-agent/venv
    # 4. 开发/旧版布局: ~/.hermes/hermes-agent/venv
    for p in \
        "/usr/local/lib/hermes-agent/venv/bin/python" \
        "$HOME/.local/lib/hermes-agent/venv/bin/python" \
        "$HERMES_VENV_DIR/bin/python" \
        "$HERMES_REPO_DIR/venv/bin/python"
    do
        [[ -x "$p" ]] && { printf '%s' "$p"; return 0; }
    done
    return 1
}

# ============================================================================
# 预检
# ============================================================================
check_prereqs() {
    log_step "阶段 0 · 预检"

    command -v git   >/dev/null 2>&1 || die "缺少 git。安装: https://git-scm.com/downloads"
    command -v curl  >/dev/null 2>&1 || die "缺少 curl。Debian/Ubuntu: sudo apt install curl"

    local os
    os="$(uname -s)"
    case "$os" in
        Linux*)  log_ok "OS: Linux" ;;
        Darwin*) log_warn "检测到 macOS — 脚本以 Linux 为主战场, macOS 也能跑但官方推荐用 Hermes Desktop installer" ;;
        MINGW*|MSYS*|CYGWIN*)
            die "Windows/Git Bash 检测到。请用官方 PowerShell 安装器: iex (irm https://hermes-agent.nousresearch.com/install.ps1)" ;;
        *) die "不支持的 OS: $os" ;;
    esac

    log_ok "git + curl 就绪"
}

# ============================================================================
# 阶段 1 · 安装 Hermes
# ============================================================================
install_hermes() {
    log_step "阶段 1 · Hermes Agent"

    if [[ "$SKIP_HERMES" == true ]]; then
        log_warn "已用 --skip-hermes 跳过 Hermes 安装"
        return 0
    fi

    if command -v hermes >/dev/null 2>&1; then
        log_ok "Hermes 已在 PATH 中 ($(hermes --version 2>/dev/null || echo '版本未知')), 跳过安装"
        return 0
    fi

    # 也检查 Hermes home 里是否有安装 (PATH 可能还没刷新)
    if [[ -x "$HERMES_REPO_DIR/hermes" ]] || [[ -x "$HERMES_BIN_DIR/hermes" ]]; then
        log_ok "Hermes 已安装在 $HERMES_HOME (当前 shell PATH 尚未刷新), 跳过安装"
        return 0
    fi

    log_info "未检测到 Hermes, 运行官方安装器..."
    log_info "  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash $HERMES_INSTALL_ARGS"

    # 构造完整管道命令字符串 (管道符不能进 bash 数组, 用字符串拼)
    # 默认 --skip-setup: 官方安装器的交互式向导由本脚本阶段 2 (configure_llm) 接管
    local install_cmd="curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup"
    if [[ -n "$HERMES_INSTALL_ARGS" ]]; then
        # shellcheck disable=SC2086
        install_cmd="curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup $HERMES_INSTALL_ARGS"
    fi

    log_info "执行: $install_cmd"
    # shellcheck disable=SC2086
    if ! bash -c "$install_cmd"; then
        log_warn "Hermes 官方安装器退出非 0 (常见于可选组件失败, 如 browser/npm)"
        # 部分成功兜底: 核心 (venv python) 就绪即可继续
        if resolve_hermes_venv_python >/dev/null 2>&1; then
            log_warn "但 Hermes 核心已就绪, 继续后续阶段; 可选组件可稍后用官方命令补装"
        else
            die "Hermes 核心安装失败。请手动排查后重试, 或参考 https://hermes-agent.nousresearch.com/docs/getting-started/installation"
        fi
    fi

    log_ok "Hermes 安装完成"
    log_info "新装后当前 shell 的 PATH 可能还没刷新, 本脚本会直接用 $HERMES_BIN_DIR 下的入口继续"
}

# ============================================================================
# 补建 hermes launcher (官方安装器可选组件失败时可能没走到建 launcher 步骤)
# ============================================================================
ensure_hermes_launcher() {
    resolve_hermes_bin >/dev/null 2>&1 && return 0   # 已有 launcher

    local py
    py=$(resolve_hermes_venv_python) || {
        log_warn "找不到 Hermes venv, 无法补建 launcher"
        return 1
    }
    # venv/bin/python → .../hermes-agent (安装目录)
    local install_dir
    install_dir=$(dirname "$(dirname "$(dirname "$py")")")
    local entry="$install_dir/hermes"
    [[ -f "$entry" ]] || {
        log_warn "找不到 Hermes 入口脚本: $entry"
        return 1
    }

    # 目标位置: 系统 bin (root 可写) → HERMES_BIN_DIR → ~/.local/bin
    local target=""
    if [[ -w /usr/local/bin ]]; then
        target="/usr/local/bin/hermes"
    elif [[ -d "$HERMES_BIN_DIR" ]]; then
        target="$HERMES_BIN_DIR/hermes"
    else
        mkdir -p "$HOME/.local/bin"
        target="$HOME/.local/bin/hermes"
    fi

    cat > "$target" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$py" "$entry" "\$@"
EOF
    chmod +x "$target"
    log_ok "已补建 hermes launcher: $target"
}

# ============================================================================
# 阶段 1.5 · 配置 LLM (交互式: 选 provider → 输入 key → 写 .env + config.yaml)
# ============================================================================
configure_llm() {
    log_step "阶段 2 · 配置 LLM 模型 & Provider"

    # --skip-llm 直接跳过
    if [[ "$SKIP_LLM" == true ]]; then
        log_warn "已用 --skip-llm 跳过 LLM 配置 — 之后请手动跑: hermes setup"
        return 0
    fi

    local hermes
    hermes=$(resolve_hermes_bin) || {
        log_warn "找不到 hermes 命令, 跳过 LLM 配置 — 装完后手动跑: hermes setup"
        return 0
    }

    # 已配置检测: config.yaml 里 model.default 已设 + 任一 key 在 .env 里
    local env_file="$HERMES_HOME/.env"
    local config_file="$HERMES_HOME/config.yaml"
    local already_configured=false
    if [[ -f "$config_file" ]] && grep -qE '^\s*default:\s*\S+' "$config_file" 2>/dev/null; then
        if [[ -f "$env_file" ]] && grep -qE '^[A-Z_]+_API_KEY=\S' "$env_file" 2>/dev/null; then
            already_configured=true
        fi
    fi
    if [[ "$already_configured" == true ]]; then
        log_ok "检测到已配置 LLM (config.yaml + .env 均有内容), 跳过"
        log_info "如需重配: hermes setup 或 hermes model"
        return 0
    fi

    # 非交互终端 (stdin 不是 tty) 无法做交互输入, 提示后跳过
    if [[ ! -t 0 ]]; then
        log_warn "非交互终端 (stdin 不是 tty), 跳过 LLM 配置 — 之后请手动跑: hermes setup"
        return 0
    fi

    printf '\n%s%sHermes 还没配 LLM, 现在来配置。%s\n' "$C_BOLD" "$C_YELLOW" "$C_RESET"
    printf '%s(已配置可 Ctrl-C 跳过, 或之后跑 hermes setup)%s\n\n' "$C_DIM" "$C_RESET"

    # --- 选 provider ---
    local provider
    if [[ -n "$LLM_PROVIDER" ]]; then
        provider="$LLM_PROVIDER"
        # 校验
        if [[ -z "${PROVIDER_ENV[$provider]:-}" ]]; then
            die "无效 provider: $provider。可选: anthropic, openai, openrouter, gemini, ollama, nous"
        fi
        log_info "使用预选 provider: $provider"
    else
        printf '%s选择 LLM Provider:%s\n' "$C_BOLD" "$C_RESET"
        printf '  %s1)%s Anthropic    (Claude, ANTHROPIC_API_KEY)\n' "$C_BLUE" "$C_RESET"
        printf '  %s2)%s OpenAI       (GPT, OPENAI_API_KEY)\n' "$C_BLUE" "$C_RESET"
        printf '  %s3)%s OpenRouter   (多模型聚合, OPENROUTER_API_KEY)\n' "$C_BLUE" "$C_RESET"
        printf '  %s4)%s Gemini       (Google, GEMINI_API_KEY)\n' "$C_BLUE" "$C_RESET"
        printf '  %s5)%s Ollama       (本地, 无需 key)\n' "$C_BLUE" "$C_RESET"
        printf '  %s6)%s Nous Portal  (OAuth, 需浏览器登录 — 会跳到 hermes setup)\n' "$C_BLUE" "$C_RESET"
        printf '  %s7)%s 自定义 OpenAI 兼容端点 (自备 base_url + key + 模型名)\n' "$C_BLUE" "$C_RESET"
        printf '  %s0)%s 跳过 LLM 配置\n' "$C_DIM" "$C_RESET"
        printf '\n选择 [0-7]: '
        local choice
        read -r choice
        case "$choice" in
            1) provider="anthropic" ;;
            2) provider="openai" ;;
            3) provider="openrouter" ;;
            4) provider="gemini" ;;
            5) provider="ollama" ;;
            6)
                log_info "Nous Portal 需浏览器 OAuth, 启动 hermes setup --portal..."
                "$hermes" setup --portal || log_warn "setup --portal 未完成, 之后可重跑"
                return 0
                ;;
            7) provider="custom" ;;
            0)
                log_warn "跳过 LLM 配置 — 装完后手动跑: hermes setup"
                return 0
                ;;
            *)
                die "无效选择: $choice"
                ;;
        esac
    fi

    local env_var="${PROVIDER_ENV[$provider]}"
    local default_model="${PROVIDER_MODEL[$provider]:-}"
    local needs_key="${PROVIDER_NEEDS_KEY[$provider]:-1}"
    local custom_base_url=""

    # --- custom (OpenAI 兼容端点): 交互输入 base_url + 模型名 ---
    if [[ "$provider" == "custom" ]]; then
        printf '\n%s输入 OpenAI 兼容端点 base_url%s (以 /v1 结尾, 不要带 /chat/completions):\n' "$C_BOLD" "$C_RESET"
        printf '  %s例如: https://api.example.com/v1%s\n> ' "$C_DIM" "$C_RESET"
        read -r custom_base_url
        if [[ -z "$custom_base_url" ]]; then
            log_warn "未输入 base_url, 跳过 LLM 配置"
            return 0
        fi
        # 用户可能照抄文档给了带 /chat/completions 的完整 URL, 自动剥离
        custom_base_url="${custom_base_url%/chat/completions}"

        printf '\n%s输入模型名%s (如 deepseek-v4-flash):\n> ' "$C_BOLD" "$C_RESET"
        read -r default_model
        if [[ -z "$default_model" ]]; then
            log_warn "未输入模型名, 跳过 LLM 配置"
            return 0
        fi
    fi

    # --- 输入 API Key (ollama 跳过) ---
    local api_key=""
    if [[ "$needs_key" == "1" ]]; then
        printf '\n%s输入 %s (%s):%s ' "$C_BOLD" "$provider" "$env_var" "$C_RESET"
        read -rs api_key
        echo  # read -rs 不回车换行, 补一个
        if [[ -z "$api_key" ]]; then
            log_warn "未输入 key, 跳过 LLM 配置 — 之后手动跑: hermes setup"
            return 0
        fi
    else
        # ollama: 用占位 key, 提示 base_url
        api_key="ollama"
        printf '%sOllama 模式: 假定本地已运行 ollama serve (默认 localhost:11434)%s\n' "$C_DIM" "$C_RESET"
    fi

    # --- 写 .env (chmod 600, 不覆盖已有行) ---
    mkdir -p "$HERMES_HOME"
    touch "$env_file"
    chmod 600 "$env_file"
    # 已有同名 key 就更新, 没有就追加
    if grep -q "^${env_var}=" "$env_file" 2>/dev/null; then
        # 用临时文件替换, 避免 sed -i 跨平台差异
        local tmp_env
        tmp_env=$(mktemp)
        # shellcheck disable=SC2002  # cat | sed 是可移植写法
        cat "$env_file" | sed "s|^${env_var}=.*|${env_var}=${api_key}|" > "$tmp_env"
        mv "$tmp_env" "$env_file"
        chmod 600 "$env_file"
    else
        printf '%s=%s\n' "$env_var" "$api_key" >> "$env_file"
    fi
    log_ok "$env_var 已写入 $env_file (权限 600)"

    # custom (OpenAI 兼容端点) 的凭据在 Hermes 中读 model.api_key (config.yaml),
    # 不读 .env — 只写 .env 会 401。key 写进 config.yaml 后收紧权限 600。
    if [[ "$provider" == "custom" ]]; then
        if "$hermes" config set model.api_key "$api_key" >/dev/null 2>&1; then
            chmod 600 "$config_file" 2>/dev/null || true
            log_ok "model.api_key 已写入 $config_file (权限 600)"
        else
            log_warn "model.api_key 设置失败, 之后手动配 (hermes config set model.api_key ...)"
        fi
    fi

    # --- 配置 model.provider + model.default (用 hermes config set, 避免手写 YAML) ---
    # custom (OpenAI 兼容端点) 在 Hermes 中就是 provider=custom + base_url
    # (cli-config.yaml.example: "custom" - Any other OpenAI-compatible endpoint)
    log_info "设置 model.provider=$provider, model.default=$default_model"
    if "$hermes" config set model.provider "$provider" >/dev/null 2>&1; then
        "$hermes" config set model.default "$default_model" >/dev/null 2>&1 \
            || log_warn "config set model.default 失败, 之后可手动 hermes model 设置"
    else
        # config set 命令不可用时回退: 直接追加到 config.yaml
        log_warn "hermes config set 不可用, 回退到直接写 config.yaml"
        {
            printf '\n# 由 install.sh 写入\n'
            printf 'model:\n'
            printf '  provider: "%s"\n' "$provider"
            printf '  default: "%s"\n' "$default_model"
        } >> "$config_file"
    fi
    # base_url 策略:
    #   custom    → 设自定义端点
    #   openrouter → 保留安装器预置的 openrouter base_url
    #   其它      → 清掉安装器预置的 openrouter base_url, 避免请求发错端点
    if [[ "$provider" == "custom" ]]; then
        "$hermes" config set model.base_url "$custom_base_url" >/dev/null 2>&1 \
            || log_warn "model.base_url 设置失败, 之后手动配"
    elif [[ "$provider" != "openrouter" ]]; then
        "$hermes" config set model.base_url "" >/dev/null 2>&1 \
            || log_warn "model.base_url 清理失败, 若对话异常请检查 config.yaml"
    fi
    log_ok "LLM 配置完成: provider=$provider, model=$default_model"

    # ollama 额外设 base_url (在清空逻辑之后, 覆盖为本地端点)
    if [[ "$provider" == "ollama" ]]; then
        "$hermes" config set model.base_url "http://localhost:11434/v1" >/dev/null 2>&1 \
            || log_warn "ollama base_url 设置失败, 之后手动配"
    fi
}

# ============================================================================
# 阶段 2 · 装 CLI 依赖到 Hermes venv
# ============================================================================
install_cli_deps() {
    log_step "阶段 3 · CLI 依赖 (typer/rich → Hermes venv)"

    local py
    if ! py=$(resolve_hermes_venv_python); then
        die "找不到 Hermes venv (尝试过 $HERMES_VENV_DIR)。请确认 Hermes 安装成功, 或用 --skip-hermes 时确保 HERMES_HOME 正确"
    fi
    log_info "Hermes venv python: $py"

    # uv 优先: 新版 Hermes venv 由 uv 创建, 通常没有 pip (No module named pip)
    local uv_cmd=""
    [[ -x "$HERMES_HOME/bin/uv" ]] && uv_cmd="$HERMES_HOME/bin/uv"
    [[ -z "$uv_cmd" ]] && uv_cmd=$(command -v uv 2>/dev/null || true)

    if [[ -n "$uv_cmd" ]]; then
        log_info "使用: $uv_cmd pip install --python $py --upgrade typer rich"
        if ! "$uv_cmd" pip install --python "$py" --upgrade typer rich >/dev/null 2>&1; then
            log_warn "uv 安装失败, 重试并显示完整输出:"
            "$uv_cmd" pip install --python "$py" --upgrade typer rich \
                || die "typer/rich 安装失败 (uv pip)"
        fi
    else
        # 回退: venv python -m pip (旧版布局带 pip)
        log_info "未找到 uv, 使用: $py -m pip install --upgrade typer rich"
        if ! "$py" -m pip install --upgrade typer rich >/dev/null 2>&1; then
            log_warn "pip 安装失败, 重试并显示完整输出:"
            "$py" -m pip install --upgrade typer rich \
                || die "typer/rich 安装失败 (pip)"
        fi
    fi
    log_ok "typer/rich 已装进 Hermes venv"
}

# ============================================================================
# 阶段 3 · 安装/更新插件
# ============================================================================
install_plugin() {
    log_step "阶段 4 · 安装 opsctl-plugin"

    local hermes
    if ! hermes=$(resolve_hermes_bin); then
        die "找不到 hermes 命令。请新开一个终端让 PATH 生效后再跑, 或检查 $HERMES_BIN_DIR"
    fi
    log_info "hermes 入口: $hermes"

    # 检查是否已安装 (先存变量再 grep: 避免 pipefail 下 grep -q 的 SIGPIPE 陷阱;
    # plugins list 是 Rich 表格, 行首带边框字符, 用行内匹配)
    local list_output
    list_output=$("$hermes" plugins list 2>/dev/null || true)
    if grep -q "$PLUGIN_NAME" <<< "$list_output"; then
        log_info "插件已安装, 执行更新: $hermes plugins update $PLUGIN_NAME"
        if ! "$hermes" plugins update "$PLUGIN_NAME"; then
            log_warn "更新失败, 尝试 --force 重装"
            "$hermes" plugins install --force --enable "$GIT_URL" \
                || die "插件重装失败"
        fi
    else
        log_info "安装插件: $hermes plugins install --enable $GIT_URL"
        if ! "$hermes" plugins install --enable "$GIT_URL"; then
            die "插件安装失败。检查 Git URL 是否可访问: $GIT_URL"
        fi
    fi

    # 确保处于 enabled 状态 (update 不会改 enabled, 重装也保险一下)
    # --no-allow-tool-override + </dev/null: 避免 tty 下交互提示挂起
    # (opsctl 不需要 override 内置工具, 拒绝该权限正好)
    if ! grep -q "$PLUGIN_NAME.*enabled" <<< "$list_output"; then
        log_info "启用插件: $hermes plugins enable --no-allow-tool-override $PLUGIN_NAME"
        "$hermes" plugins enable --no-allow-tool-override "$PLUGIN_NAME" < /dev/null \
            || die "插件启用失败"
    fi
    log_ok "插件 $PLUGIN_NAME 已安装并启用"
}

# ============================================================================
# 阶段 4 · 重启 gateway
# ============================================================================
restart_gateway() {
    if [[ "$NO_RESTART" == true ]]; then
        log_warn "已用 --no-restart 跳过 gateway 重启 (需手动 restart 插件才生效)"
        return 0
    fi

    log_step "阶段 5 · 重启 Hermes gateway"
    local hermes
    hermes=$(resolve_hermes_bin) || return 0

    # 纯 bash 超时 (30s): gateway 未运行过时 restart 可能挂起, 不能阻塞安装
    "$hermes" gateway restart >/dev/null 2>&1 &
    local gw_pid=$!
    local i=0
    while kill -0 "$gw_pid" 2>/dev/null && [[ $i -lt 30 ]]; do
        sleep 1; i=$((i + 1))
    done
    if kill -0 "$gw_pid" 2>/dev/null; then
        kill "$gw_pid" 2>/dev/null
        log_warn "gateway restart 超时 (30s) — 不影响安装, 首次启动 gateway 时插件会自动加载"
    else
        wait "$gw_pid"
        log_ok "gateway 已重启"
    fi
}

# ============================================================================
# 阶段 5 · 验证
# ============================================================================
verify() {
    log_step "阶段 6 · 验证"

    local hermes
    hermes=$(resolve_hermes_bin) || { log_warn "找不到 hermes 命令, 跳过验证 (请新开终端后手动验证)"; return 0; }

    # 列表里 enabled (Rich 表格, 行内匹配; 先存变量避免 pipefail SIGPIPE 陷阱)
    local list_output
    list_output=$("$hermes" plugins list 2>/dev/null || true)
    if grep -q "$PLUGIN_NAME.*enabled" <<< "$list_output"; then
        log_ok "plugins list: $PLUGIN_NAME = enabled"
    else
        log_warn "plugins list 未显示 $PLUGIN_NAME 为 enabled — 请检查: $hermes plugins list"
    fi

    # 通过 shim 直接验 CLI (不依赖 gateway)
    local plugin_dir="$HERMES_HOME/plugins/$PLUGIN_NAME"
    if [[ -f "$plugin_dir/bin/opsctl_shim.py" ]]; then
        local py
        if py=$(resolve_hermes_venv_python); then
            log_info "测试 opsctl CLI..."
            if "$py" "$plugin_dir/bin/opsctl_shim.py" resource types >/dev/null 2>&1; then
                log_ok "opsctl resource types 执行成功"
            else
                log_warn "opsctl CLI 测试失败 — 手动跑: $py $plugin_dir/bin/opsctl_shim.py resource types"
            fi
        fi
    fi
}

# ============================================================================
# 卸载
# ============================================================================
do_uninstall() {
    log_step "卸载 opsctl-plugin (保留 Hermes)"

    local hermes
    if ! hermes=$(resolve_hermes_bin); then
        log_warn "找不到 hermes 命令 — 尝试直接删目录"
    else
        local list_output
        list_output=$("$hermes" plugins list 2>/dev/null || true)
        if grep -q "$PLUGIN_NAME" <<< "$list_output"; then
            log_info "卸载: $hermes plugins remove $PLUGIN_NAME"
            "$hermes" plugins remove "$PLUGIN_NAME" || log_warn "插件移除失败, 将尝试手动删目录"
        else
            log_info "插件未在 hermes plugins list 中, 跳过"
        fi
    fi

    local plugin_dir="$HERMES_HOME/plugins/$PLUGIN_NAME"
    if [[ -d "$plugin_dir" ]]; then
        log_info "删除 $plugin_dir"
        rm -rf "$plugin_dir"
    fi
    log_ok "卸载完成"
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    if [[ "$UNINSTALL" == true ]]; then
        do_uninstall
        exit 0
    fi

    printf '%s%s╔══════════════════════════════════════════════╗%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
    printf '%s%s║  opsctl-plugin 一键安装                       ║%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
    printf '%s%s╚══════════════════════════════════════════════╝%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
    log_info "Git URL:   $GIT_URL"
    log_info "HERMES_HOME: $HERMES_HOME"

    check_prereqs
    install_hermes
    ensure_hermes_launcher
    configure_llm
    install_cli_deps
    install_plugin
    restart_gateway
    verify

    log_step "完成"
    printf '%s%sopsctl-plugin 安装成功!%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
    printf '\n%s下一步:%s\n' "$C_DIM" "$C_RESET"
    printf '  • 新开一个终端 (让 PATH 刷新), 跑:  hermes\n'
    printf '  • 或直接验证:                       hermes plugins list\n'
    printf '  • 让 Agent 调用:                    hermes chat -q "使用opsctl查看资源类型"\n'
    printf '\n%s重配 LLM:%s  hermes setup 或 hermes model\n' "$C_DIM" "$C_RESET"
    printf '%s如需卸载:%s  ./install.sh --uninstall\n' "$C_DIM" "$C_RESET"
}

main "$@"
