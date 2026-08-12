# opsctl — 运维资源元数据管理 CLI

为 [Hermes Agent](https://hermes-agent.nousresearch.com/) 提供运维场景缺失的**资源元数据层**：资源清单、凭据、依赖关系、关注点（过期/水位）。

运维 Agent 没有持久记忆：连接信息靠每次对话传递、凭据散落各处、服务间依赖只存在于上下文里，换一个会话就归零。opsctl 用结构化元数据把"是什么 + 怎么连 + 谁依赖谁 + 何时到期"沉淀下来，让 Agent 基于事实决策，而不是凭对话上下文瞎猜。

## 设计哲学

- **万物皆资源**：阿里云账号、ECS、Redis、MySQL、PG、HBase、自部署服务都是 `Resource` 的子类。
- **只存"是什么 + 怎么连"，不存"怎么用"**：具体操作能力（如 psql 的功能）以纯文本 `operations_guide` 告诉 Agent，不结构化进 schema，保护扩展性。
- **关系只管依赖**：无分组、无标签。含环路检测。
- **关注点配合 Hermes Cron**：证书过期、续费日期等可挂关注点，由 Hermes 定时巡检。

## 安装

需要 **Python ≥ 3.11**（唯一运行时依赖：typer / rich）。

```bash
# 开发模式
pip install -e ".[dev]"

# 普通安装 (仅 CLI, 终端使用)
pip install .
```

数据默认写入 `data/opsctl.db`（不进 git）。可用 `OPSCTL_DB` 环境变量覆盖路径。

> Hermes 插件集成不走 pip, 见下方 [Hermes Plugin 集成](#hermes-plugin-集成)。

## CLI 用法

```bash
# 列出所有已注册资源类型
opsctl resource types

# 新增一台 ECS
opsctl resource add --type ecs --name web1 \
  --attr host=10.0.0.1 --attr ssh_user=root \
  --attr ssh_password:secret=s3cret

# 列出资源
opsctl resource list
opsctl resource list --type redis

# 查看详情（默认 secret 脱敏）
opsctl resource show web1
opsctl resource show web1 --reveal   # 显示凭据明文

# 更新扩展属性
opsctl resource update web1 --attr owner=team-a

# 删除（被引用时拒绝，--force 强制级联清理）
opsctl resource delete web1
opsctl resource delete web1 --force

# 依赖关系（含环路检测，类型固定 depends_on）
opsctl relation add --source web1 --target redis-cache
opsctl relation list
opsctl relation graph redis-cache        # 上下游拓扑

# 关注点（配合 Hermes Cron）
# 资源创建时已按类型自动挂默认监控项（如 ECS: CPU/内存/磁盘水位），下面再挂自定义项
opsctl concern add --resource web1 --category expiry \
  --desc "SSL证书过期" --due 2026-12-31 --severity critical
opsctl concern list
opsctl concern due --within 7d       # 7天内到期的 open 项
```

所有命令支持 `--json` 全局开关，输出机器可读 JSON（Plugin/Agent 用）。

## Hermes Plugin 集成

opsctl 同时是一个 Hermes Plugin（目录插件形态），把上述能力暴露为 LLM 可调用工具。
插件与 CLI 通过 subprocess 解耦，**CLI 源码随插件仓库一起分发**，`hermes plugins update`
一条命令即可同时更新插件与 CLI。

### 启用步骤

1. 从 Git 仓库安装插件（本仓库即插件源，支持任意 Git URL）：

   ```bash
   hermes plugins install https://github.com/<your-org>/DevOps-Agent.git
   ```

2. 按 `after-install.md` 提示完成一次性依赖安装（Hermes venv 装 `typer rich`）。
3. 启用插件：

   ```bash
   hermes plugins enable opsctl-plugin
   ```

4. 重启 Hermes，Agent 即可调用以下工具：

   | 工具 | 用途 |
   |------|------|
   | `ops_list_resources` | 列出资源清单 |
   | `ops_show_resource` | 查看资源详情（含凭据 + 操作指南，默认脱敏） |
   | `ops_add_resource` | 登记新资源（含连接凭据） |
   | `ops_update_resource` | 更新资源字段或属性（覆盖语义） |
   | `ops_delete_resource` | 删除资源（被引用时拒绝，force 级联） |
   | `ops_list_resource_types` | 列出资源类型 |
   | `ops_add_relation` | 登记依赖（含环路检测） |
   | `ops_delete_relation` | 删除依赖关系 |
   | `ops_relation_graph` | 查看依赖拓扑 |
   | `ops_list_concerns` | 列出关注点 |
   | `ops_add_concern` | 添加关注点（到期/水位等） |
   | `ops_resolve_concern` | 将关注点标记为已解决 |
   | `ops_concerns_due` | 查询即将到期关注点 |

插件场景下数据库按 profile 隔离：数据写入 `$HERMES_HOME/data/opsctl.db`
（各 profile 数据独立，不落在仓库目录），可用 `OPSCTL_DB` 环境变量强制指定路径。

Plugin handler 永远通过 subprocess 调 `opsctl --json`，与 CLI 解耦。目录插件模式下
优先调用仓库内 `bin/opsctl_shim.py`（CLI 随插件 git 更新），退回 PATH 中的独立安装 CLI。

### 更新

```bash
hermes plugins update opsctl-plugin   # 插件 + CLI 一次更新
```

### 卸载

```bash
hermes plugins remove opsctl-plugin
```

### 定时巡检（Hermes Cron）

Plugin 注册了 `/ops-inspect` slash 命令，用于检查窗口内（默认 30 天）到期的关注点，并按"需立即处理/需关注/其余折叠"三组报告。要让它定时运行：

在 Hermes 中说一句**自然语言**，它就会记住 Cron 任务：

```text
每天早上 9 点执行 /ops-inspect
```

Hermes 会自动创建 Cron 定时任务。不需要手动编辑配置文件或写 cron 表达式。

如果以后想改时间：

```text
把巡检时间改到每天下午 3 点
```

想查看已设置的定时任务：

```text
查看我的定时任务
```

## 扩展自定义资源类型

在任意模块定义子类并 `@register_resource`，无需改动其它文件：

```python
from opsctl.models import Resource, register_resource

@register_resource
class KafkaResource(Resource):
    type = "kafka"
    standard_attributes = {
        "bootstrap_servers": {"type": "str", "required": True},
        "sasl_password": {"type": "secret"},
    }
    operations_guide = "连接: kafka-console-consumer/consumer.sh ..."
```

只要该模块被 import（如加入 `builtin_types` 或自定义加载入口），CLI 立即识别新类型。

## 测试

```bash
pytest tests/ -v
ruff check src/ tests/
```

## 约束（来自 spec）

- 不对接任何云厂商 API —— 交给 Agent 自己用 `aliyun cli` 等工具。
- 凭据明文存 SQLite，仅 `value_type: secret` 标记用于 CLI 显示脱敏。
- 不修改 Hermes 核心代码 —— 只走 Plugin 路径。
