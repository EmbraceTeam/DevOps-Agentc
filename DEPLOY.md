# opsctl 部署指南

## 环境要求

- Hermes Agent（已部署运行，支持 `hermes plugins` 子命令）
- Hermes 服务器可访问 opsctl 的 Git 仓库（Codeup）
- Python 3.11+（CLI 终端使用，可选）

## 部署架构

opsctl 以 **Hermes 目录插件** 形态分发：本仓库即插件源，`hermes plugins install`
克隆到 `~/.hermes/plugins/opsctl-plugin/`。CLI 源码（`src/opsctl`）随仓库一起分发，
插件 handler 通过 `bin/opsctl_shim.py` 调用仓库内 CLI —— **`hermes plugins update`
一条命令同时更新插件与 CLI**，无需再手动打包上传 wheel。

## 快速部署

### 一键部署（推荐）

```bash
bash deploy.sh --remote user@host --profiles "ops eog"
```

脚本自动完成：安装插件（默认 profile）→ 其他 profile 建 symlink → 逐 profile 启用 → 逐 profile 重启。

### 手动部署

在 Hermes 服务器上执行：

```bash
# 1. 安装插件 (克隆本仓库到 ~/.hermes/plugins/opsctl-plugin/)
hermes plugins install https://github.com/<your-org>/DevOps-Agent.git

# 2. 一次性安装 CLI 依赖 (Hermes venv)
$HERMES_VENV/bin/pip install typer rich

# 3. 启用插件
hermes plugins enable opsctl-plugin

# 4. 重启 Hermes
hermes gateway restart
```

> `$HERMES_VENV` 是 Hermes 的虚拟环境路径（如 `/home/<user>/.hermes/hermes-agent/venv`）。
> 安装后 Hermes 会渲染 `after-install.md` 提示上述步骤。

## 多 profile 部署

Hermes 的插件目录是 **per-profile** 的：`hermes plugins install` 只装到当前
profile 的 `plugins/`（默认 profile 为 `~/.hermes/plugins/`，命名 profile 为
`~/.hermes/profiles/<name>/plugins/`），其他 profile 的 gateway 看不到。

**方案：默认 profile 安装一次，其他 profile 用 symlink 共享**（更新只需一次，
`hermes plugins update` 的 git pull 会跟随 symlink）：

```bash
# 1. 默认 profile 安装
hermes plugins install https://github.com/<your-org>/DevOps-Agent.git

# 2. 其他 profile 建 symlink (以 ops/eog 为例)
mkdir -p ~/.hermes/profiles/ops/plugins ~/.hermes/profiles/eog/plugins
ln -sf ~/.hermes/plugins/opsctl-plugin ~/.hermes/profiles/ops/plugins/opsctl-plugin
ln -sf ~/.hermes/plugins/opsctl-plugin ~/.hermes/profiles/eog/plugins/opsctl-plugin

# 3. 配置技能树 external_dirs (agent 可见 ops-inspect 技能)
#    插件 register_skill 注册的技能不进 <available_skills> 索引 (Hermes 设计),
#    需在 config.yaml 的 skills.external_dirs 指向插件 skills 目录:
#    默认: ~/.hermes/config.yaml
#    ops:  ~/.hermes/profiles/ops/config.yaml
#    eog:  ~/.hermes/profiles/eog/config.yaml
#    skills:
#      external_dirs:
#        - ~/.hermes/plugins/opsctl-plugin/src/opsctl/plugin/skills

# 4. 逐 profile 启用 + 重启
hermes plugins enable opsctl-plugin
hermes --profile ops plugins enable opsctl-plugin
hermes --profile eog plugins enable opsctl-plugin
hermes gateway restart
hermes --profile ops gateway restart
hermes --profile eog gateway restart
```

`bash deploy.sh --profiles "ops eog"` 会自动完成上述全部步骤（含 external_dirs 配置）。

各 profile 的数据库仍按 `HERMES_HOME` 自动隔离（见下），互不影响。

> 卸载时先移除各 profile config.yaml 的 `skills.external_dirs` 项，再 `hermes plugins remove`（目录不存在时 Hermes 会自动跳过，不报错）。

### 终端 CLI（可选）

如需在服务器终端直接使用 `opsctl` 命令：

```bash
pip3 install --break-system-packages --ignore-installed ~/opsctl-0.1.0-py3-none-any.whl
# 或从仓库安装: pip install git+https://github.com/<your-org>/DevOps-Agent.git
```

## 升级

```bash
hermes plugins update opsctl-plugin   # git pull, 插件 + CLI 一次更新
hermes gateway restart                # 重启生效
```

## 验证部署

### 检查 Plugin 加载

```bash
hermes plugins list
```

输出中应包含：

```text
opsctl-plugin | enabled | 0.1.0 | 运维资源元数据管理 CLI — 资源清单/凭据/依赖/关注点 (Hermes 插件)
```

### 检查 CLI（经 shim）

```bash
python3 ~/.hermes/plugins/opsctl-plugin/bin/opsctl_shim.py resource types
```

应输出 12 种资源类型：

```text
aliyun_account
apisix
dockerswarm
ecs
etcd
hbase
k8s
keycloak
mysql
postgres
redis
service
```

### 测试工具调用

```bash
hermes chat -q "使用opsctl查看资源类型"
```

Agent 应能调用 `ops_list_resource_types` 工具并返回类型列表。

## 数据库隔离

opsctl 自动根据 `HERMES_HOME` 环境变量隔离数据库路径：

| profile | 数据库路径 |
|---------|-----------|
| `ops` | `$HERMES_HOME/data/opsctl.db` |
| `eog` | `$HERMES_HOME/data/opsctl.db` |
| 直接 CLI（无 Hermes） | `data/opsctl.db`（CWD 相对路径） |

如需手动指定路径，设置环境变量：

```bash
export OPSCTL_DB=/path/to/custom.db
```

## Schema 自动迁移

首次使用旧版数据库时，opsctl 会自动检测并执行 schema 迁移。迁移记录保存在 `_schema_version` 表中。用户无感，自动完成。

## 定时巡检

Plugin 注册了 `/ops-inspect` slash 命令。在 Hermes 中告诉它：

```text
每天早上 9 点执行 /ops-inspect
```

Hermes 会自动创建 Cron 定时任务，检查窗口内（默认 30 天）到期的关注点，并按"需立即处理/需关注/其余折叠"三组报告。

## 从旧版（pip entry-point 插件）迁移

旧版通过 `hermes_agent.plugins` entry point 分发，升级到目录插件形态：

```bash
# 1. 卸载旧 pip 包 (移除 entry point, 避免双插件冲突)
$HERMES_VENV/bin/pip uninstall opsctl

# 2. 按"快速部署"安装目录插件
hermes plugins install https://github.com/<your-org>/DevOps-Agent.git
$HERMES_VENV/bin/pip install typer rich
hermes plugins enable opsctl-plugin

# 3. 移除旧配置项 (若存在)
#    编辑 ~/.hermes/config.yaml, 将 plugins.enabled 中的 opsctl 改为 opsctl-plugin

# 4. 重启
hermes gateway restart
```

## 常见问题

**Q: Plugin 加载失败，日志显示 `Failed to load plugin 'opsctl-plugin'`**

A: 检查插件目录是否完整：

```bash
ls ~/.hermes/plugins/opsctl-plugin/   # 应有 plugin.yaml 和 __init__.py
```

如不完整，`hermes plugins install --force <git-url>` 重装。

**Q: 工具调用报 `调用 opsctl 失败: [Errno 2] No such file or directory`**

A: Hermes venv 缺少 typer/rich，或 shim 路径异常。先验证 shim：

```bash
$HERMES_VENV/bin/python ~/.hermes/plugins/opsctl-plugin/bin/opsctl_shim.py resource types
```

**Q: Agent 说找不到 `ops_delete_relation` 等工具**

A: 确认 Hermes gateway 已重启（旧进程可能还在跑旧代码）：

```bash
hermes gateway restart
```

**Q: 更新后工具行为没变化**

A: `hermes plugins update` 后必须重启 gateway 才生效。
