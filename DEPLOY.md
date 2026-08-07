# opsctl 部署指南

## 环境要求

- Hermes Agent（已部署运行）
- Python 3.11+
- `scp` + `ssh` 访问 Hermes 服务器

## 快速部署

### 一步部署（推荐）

在本机项目目录运行：

```bash
bash deploy.sh
```

脚本会：

1. 打包 opsctl wheel
2. 询问 Hermes 服务器地址
3. 上传 wheel 和 SOUL.md 到服务器
4. 安装 opsctl
5. 启用 Hermes Plugin
6. 安装运维工程师角色提示词（SOUL.md）

### 手动部署

如果 `deploy.sh` 不适用，按以下步骤手动操作：

#### 1. 打包

```bash
cd /path/to/DevOps-Agent
uv build --wheel
```

生成文件：`dist/opsctl-0.1.0-py3-none-any.whl`

#### 2. 上传到服务器

```bash
scp dist/opsctl-0.1.0-py3-none-any.whl \
    src/opsctl/plugin/contexts/ops-engineer/SOUL.md \
    user@server:~/
```

#### 3. 安装 opsctl

SSH 登录服务器后：

```bash
# Hermes 虚拟环境安装（使 Plugin 可用）
HERMES_VENV="/home/<user>/.hermes/hermes-agent/venv"
$HERMES_VENV/bin/pip install --break-system-packages --no-deps --force-reinstall ~/opsctl-0.1.0-py3-none-any.whl

# 系统环境安装（使 CLI 可用）
pip3 install --break-system-packages --ignore-installed ~/opsctl-0.1.0-py3-none-any.whl
```

#### 4. 部署巡检技能

```bash
# skills 目录需要手动部署（不在 wheel 中）
# 将项目中的 skills 目录复制到 Plugin 目录
PLUGIN_DIR=$(find $HERMES_VENV/lib -path "*/opsctl/plugin" -type d)
cp -r /path/to/src/opsctl/plugin/skills $PLUGIN_DIR/
```

#### 5. 配置 Hermes Plugin

编辑 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled: [opsctl]
```

各 profile 的配置文件：

- `ops` profile: `/home/<user>/.hermes/profiles/ops/config.yaml`
- `eog` profile: `/home/<user>/.hermes/profiles/eog/config.yaml`

#### 6. 安装角色提示词（可选）

```bash
# ops profile
cp ~/SOUL.md /home/<user>/.hermes/profiles/ops/SOUL.md

# eog profile
cp ~/SOUL.md /home/<user>/.hermes/profiles/eog/SOUL.md
```

#### 7. 重启 Hermes

```bash
# ops profile
hermes --profile ops gateway restart

# eog profile
hermes --profile eog gateway restart
```

## 验证部署

### 检查 opsctl CLI

```bash
opsctl resource types
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

### 检查 Plugin 加载

```bash
hermes plugins list
```

输出中应包含：

```text
opsctl   | enabled | 0.1.0 | 运维资源元数据管理 CLI | entrypoint
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

## 升级

重复上述部署步骤即可。`--force-reinstall` 会覆盖旧版本。重启 Hermes 后生效。

## 常见问题

**Q: Plugin 加载失败，日志显示 `Failed to load plugin 'opsctl'`**

A: 检查 opsctl 是否已安装到 Hermes 的虚拟环境中：

```bash
$HERMES_VENV/bin/pip list | grep opsctl
```

如未安装，重复步骤 3。

**Q: Agent 说找不到 `ops_delete_relation` 等工具**

A: 确认 Hermes gateway 已重启（旧进程可能还在跑旧代码）：

```bash
hermes --profile ops gateway restart
```

**Q: opsctl CLI 显示的类型数量不对**

A: 可能是系统级安装版本过旧，重新安装：

```bash
pip3 install --break-system-packages --ignore-installed ~/opsctl-0.1.0-py3-none-any.whl
```
