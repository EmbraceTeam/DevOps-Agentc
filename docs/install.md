# install.sh 使用文档

一键安装 **Hermes Agent + opsctl-plugin** 全链路脚本。从零到可用：安装 Hermes → 交互式配置 LLM → 安装并启用本插件 → 验证。

## 快速开始

```bash
# 方式一: 直接执行 (默认安装 GitHub 公开仓库)
curl -fsSL https://raw.githubusercontent.com/EmbraceTeam/DevOps-Agentc/master/install.sh | bash

# 方式二: clone 仓库后本地执行 (推荐, 便于传参和查看脚本)
git clone https://github.com/EmbraceTeam/DevOps-Agentc.git
cd DevOps-Agent
./install.sh
```

脚本**幂等**：重复执行安全，已完成的阶段自动跳过（Hermes 已装 / LLM 已配 / 插件已装均会跳过）。

## 完整流程

| 阶段 | 动作 | 说明 |
|------|------|------|
| 0 · 预检 | 检查 git / curl / OS | Linux 为主战场；macOS 警告（推荐官方 Desktop installer）；Windows/Git Bash 拒绝并指路 PowerShell 安装器 |
| 1 · 安装 Hermes | 运行官方安装器 | 已装则跳过；失败但核心就绪（venv 存在）则继续，可选组件（browser/npm）失败不阻塞 |
| 2 · 配置 LLM | 交互式选择 provider + 输入 key | 已配置自动跳过；非交互终端自动跳过 |
| 3 · CLI 依赖 | `uv pip install typer rich` 到 Hermes venv | opsctl CLI 运行时依赖，自动探测 venv 位置并补建 launcher |
| 4 · 安装插件 | `hermes plugins install --enable` | 已装则 `plugins update`；自动处理 enable 状态 |
| 5 · 重启 gateway | `hermes gateway restart` | 30s 超时保护；失败/超时不阻塞安装 |
| 6 · 验证 | plugins list + opsctl CLI 实测 | 任一失败仅告警不中断 |

## LLM 配置交互说明

阶段 2 会展示 provider 选择菜单：

```text
1) Anthropic        (Claude, ANTHROPIC_API_KEY)
2) OpenAI           (GPT, OPENAI_API_KEY)
3) OpenRouter       (多模型聚合, OPENROUTER_API_KEY)
4) Gemini           (Google, GEMINI_API_KEY)
5) Ollama           (本地, 无需 key)
6) Nous Portal      (OAuth, 需浏览器登录 — 自动跳 hermes setup --portal)
7) 自定义 OpenAI 兼容端点 (自备 base_url + key + 模型名)
0) 跳过 LLM 配置
```

API Key 通过 `read -rs` **静默输入**：不回显终端、不进 shell history。写入 `~/.hermes/.env`（权限 600）。

### 自定义 OpenAI 兼容端点（选项 7）

适合自建网关 / 第三方 OpenAI 兼容服务。按提示依次输入：

1. **base_url** — 以 `/v1` 结尾，如 `https://api.example.com/v1`。如果照抄了带 `/chat/completions` 的完整 URL，脚本会自动剥离。
2. **模型名** — 如 `deepseek-v4-flash`。
3. **API Key** — 静默输入。

写入位置（与内置 provider 不同）：

| 配置 | 位置 |
|------|------|
| base_url | `~/.hermes/config.yaml` → `model.base_url` |
| 模型 | `~/.hermes/config.yaml` → `model.default` |
| **API Key** | `~/.hermes/config.yaml` → `model.api_key`（Hermes custom provider 的凭据字段，不读 .env；写入后 config.yaml 权限收紧为 600） |

> 注意：custom 端点配置后，`hermes config set model.provider custom` 已设好，直接 `hermes chat` 即可。

## 参数一览

```text
./install.sh [OPTIONS]

--git-url <url>              插件源 Git URL (默认: https://github.com/EmbraceTeam/DevOps-Agentc.git)
--skip-hermes                跳过 Hermes 安装 (本机已装时用)
--skip-llm                   跳过 LLM 配置阶段 (之后手动跑 hermes setup)
--provider <name>            预选 LLM provider, 跳过选择菜单 (仍会提示输入 key)
                             可选: anthropic, openai, openrouter, gemini, ollama, nous, custom
--no-restart                 装完不执行 hermes gateway restart
--uninstall                  卸载插件 (保留 Hermes)
--hermes-install-args="..."  透传给 Hermes 官方安装器 (如 --skip-browser)
-h, --help                   显示帮助
```

## 环境变量

| 变量 | 作用 |
|------|------|
| `HERMES_HOME` | Hermes 数据目录（默认 `~/.hermes`） |
| `UV_INDEX_URL` | pip/uv 软件源（国内建议设为 PyPI 镜像，见下文） |
| `NO_COLOR` | 禁用颜色输出 |

## 国内网络加速建议

脚本本身会走 Hermes 官方安装器（国外源）。在国内网络下，建议：

```bash
# 1. 系统包管理器换国内镜像 (Debian/Ubuntu 示例: 清华 TUNA)
sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources
apt-get update

# 2. Hermes 依赖安装走国内 PyPI 镜像
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./install.sh
```

容器 / CI 环境下同理（Docker 容器实测通过此组合完成全链路安装）。

## 常见问题

### `hermes: command not found`

安装器写入了 launcher 但当前 shell 的 PATH 未刷新。**新开一个终端**，或手动执行 `export PATH="$HOME/.local/bin:$PATH"`。

### gateway restart 显示超时 / 失败

正常 — 首次安装时 gateway 尚未运行过。`hermes gateway restart` 有 30s 超时保护，不影响安装。首次启动 gateway（`hermes gateway`）时插件自动加载。

### 官方安装器报 npm / browser 安装失败

Hermes 核心（Python venv + CLI）已装好即可继续，脚本会自动跳过失败并提示。browser 等可选组件可稍后补装（`hermes update` 或重新运行官方安装器）。

### 自定义端点对话报 HTTP 401 Invalid API key

确认 key 已写入 `~/.hermes/config.yaml` 的 `model.api_key`（custom provider 的凭据字段），且 config.yaml 权限为 600：

```bash
hermes config set model.api_key "sk-xxx"
chmod 600 ~/.hermes/config.yaml
```

### 重复运行会不会重复装？

不会。Hermes 已装 → 跳过；LLM 已配（config.yaml + .env 有内容）→ 跳过；插件已装 → 走 `plugins update` 更新到最新。

### `--provider custom` 配合非交互环境

非交互终端（stdin 不是 tty）会自动跳过 LLM 配置，不会卡死。之后手动执行 `hermes setup` 即可。

## 卸载

```bash
./install.sh --uninstall
```

卸载 opsctl-plugin（`hermes plugins remove` + 清理插件目录），**保留 Hermes 本体**。
