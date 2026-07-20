"""opsctl 工具的 LLM-visible schema 声明.

``PLUGIN_TOOLS``: tool_name -> {"schema": <OpenAI function schema>, "cli_args": <build args>}.
``cli_args`` 是一个函数: (params: dict) -> list[str], 把 LLM 入参转成 opsctl 命令行.
"""

from __future__ import annotations


def _list_resources_args(params: dict) -> list[str]:
    args = ["resource", "list", "--json"]
    if params.get("type"):
        args += ["--type", params["type"]]
    return args


def _show_resource_args(params: dict) -> list[str]:
    args = ["resource", "show", "--json"]
    # 默认不暴露凭据; 仅当 LLM 显式 reveal=true 时才带 --reveal
    if params.get("reveal"):
        args.append("--reveal")
    args.append(params["name"])
    return args


def _add_resource_args(params: dict) -> list[str]:
    args = ["resource", "add", "--json", "--type", params["type"], "--name", params["name"]]
    if params.get("endpoint"):
        args += ["--endpoint", params["endpoint"]]
    if params.get("port") is not None:
        args += ["--port", str(params["port"])]
    for attr in params.get("attributes", []):
        args += ["--attr", attr]
    return args


def _list_types_args(_params: dict) -> list[str]:
    return ["resource", "types", "--json"]


def _update_resource_args(params: dict) -> list[str]:
    """构建 resource update 命令.

    name 是位置参, 传什么直接覆盖. 未传的字段保持原值 (spec: 覆盖语义).
    """
    args = ["resource", "update", "--json", params["name"]]
    if params.get("endpoint") is not None:
        args += ["--endpoint", params["endpoint"]]
    if params.get("port") is not None:
        args += ["--port", str(params["port"])]
    if params.get("status"):
        args += ["--status", params["status"]]
    if params.get("description") is not None:
        args += ["--description", params["description"]]
    for attr in params.get("attributes", []):
        args += ["--attr", attr]
    return args


def _delete_resource_args(params: dict) -> list[str]:
    args = ["resource", "delete", "--json", params["name"]]
    if params.get("force"):
        args.append("--force")
    return args


def _add_relation_args(params: dict) -> list[str]:
    args = [
        "relation",
        "add",
        "--json",
        "--source",
        params["source"],
        "--target",
        params["target"],
    ]
    return args


def _delete_relation_args(params: dict) -> list[str]:
    return [
        "relation",
        "delete",
        "--json",
        "--source",
        params["source"],
        "--target",
        params["target"],
    ]


def _relation_graph_args(params: dict) -> list[str]:
    return ["relation", "graph", "--json", params["name"]]


def _list_concerns_args(params: dict) -> list[str]:
    args = ["concern", "list", "--json"]
    if params.get("resource"):
        args += ["--resource", params["resource"]]
    if params.get("status"):
        args += ["--status", params["status"]]
    return args


def _concerns_due_args(params: dict) -> list[str]:
    return ["concern", "due", "--json", "--within", params["within"]]


def _add_concern_args(params: dict) -> list[str]:
    args = [
        "concern",
        "add",
        "--json",
        "--resource",
        params["resource"],
        "--category",
        params["category"],
        "--desc",
        params["description"],
    ]
    if params.get("due"):
        args += ["--due", params["due"]]
    if params.get("severity"):
        args += ["--severity", params["severity"]]
    return args


def _resolve_concern_args(params: dict) -> list[str]:
    return ["concern", "resolve", "--json", str(params["id"])]


PLUGIN_TOOLS: dict[str, dict] = {
    "ops_list_resources": {
        "schema": {
            "name": "ops_list_resources",
            "description": (
                "列出所有已登记的运维资源 (服务器/数据库/服务等). 可按类型筛选. "
                "返回含 id/name/type/endpoint/port/status 的资源数组."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": (
                            "资源类型筛选: ecs/redis/postgres/mysql/hbase/aliyun_account/service 等"
                        ),
                    }
                },
                "required": [],
            },
        },
        "cli_args": _list_resources_args,
    },
    "ops_show_resource": {
        "schema": {
            "name": "ops_show_resource",
            "description": (
                "查看单个资源详情, 含属性与 operations_guide 操作指南. "
                "默认 secret 字段脱敏; 需要凭据明文执行连接时显式传 reveal=true. "
                "决策运维操作前应先调用本工具确认连接方式."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "资源 id 或 name"},
                    "reveal": {
                        "type": "boolean",
                        "description": "是否显示 secret 字段明文, 默认 false",
                    },
                },
                "required": ["name"],
            },
        },
        "cli_args": _show_resource_args,
    },
    "ops_add_resource": {
        "schema": {
            "name": "ops_add_resource",
            "description": (
                "登记一条新资源 (含连接凭据). attributes 形如 ['host=1.2.3.4','ssh_user=root']."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "资源类型"},
                    "name": {"type": "string", "description": "唯一资源名"},
                    "endpoint": {"type": "string"},
                    "port": {"type": "integer"},
                    "attributes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "key[:type]=value 形式, 如 password:secret=xxx",
                    },
                },
                "required": ["type", "name"],
            },
        },
        "cli_args": _add_resource_args,
    },
    "ops_list_resource_types": {
        "schema": {
            "name": "ops_list_resource_types",
            "description": "列出所有已注册的资源类型, 用于了解可登记哪些资源.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "cli_args": _list_types_args,
    },
    "ops_add_relation": {
        "schema": {
            "name": "ops_add_relation",
            "description": "登记依赖关系: source 依赖 target. 含环路检测, 形成循环会被拒绝.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "依赖方资源 id 或 name"},
                    "target": {"type": "string", "description": "被依赖方资源 id 或 name"},
                },
                "required": ["source", "target"],
            },
        },
        "cli_args": _add_relation_args,
    },
    "ops_delete_relation": {
        "schema": {
            "name": "ops_delete_relation",
            "description": (
                "删除一条依赖关系 (source 依赖 target). 用于清理错误登记或已下线服务的关系."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "依赖方资源 id 或 name"},
                    "target": {"type": "string", "description": "被依赖方资源 id 或 name"},
                },
                "required": ["source", "target"],
            },
        },
        "cli_args": _delete_relation_args,
    },
    "ops_relation_graph": {
        "schema": {
            "name": "ops_relation_graph",
            "description": "查看某资源的上下游依赖拓扑, 用于变更前的影响分析.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "资源 id 或 name"}},
                "required": ["name"],
            },
        },
        "cli_args": _relation_graph_args,
    },
    "ops_list_concerns": {
        "schema": {
            "name": "ops_list_concerns",
            "description": "列出资源的关注点 (如证书过期/水位告警), 默认仅 open 状态.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string"},
                    "status": {"type": "string", "description": "open/resolved/snoozed"},
                },
                "required": [],
            },
        },
        "cli_args": _list_concerns_args,
    },
    "ops_concerns_due": {
        "schema": {
            "name": "ops_concerns_due",
            "description": (
                "查询时间窗口内到期的 open 关注点, 配合定时任务巡检. within 格式如 7d/12h/30m."
            ),
            "parameters": {
                "type": "object",
                "properties": {"within": {"type": "string"}},
                "required": ["within"],
            },
        },
        "cli_args": _concerns_due_args,
    },
    "ops_update_resource": {
        "schema": {
            "name": "ops_update_resource",
            "description": (
                "更新资源的主表字段或属性. 覆盖语义: 传了的字段替换, 未传的保持原值. "
                "更新成功后返回完整的最新资源 JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "资源 id 或 name"},
                    "endpoint": {"type": "string", "description": "新 endpoint, 传空字符串清除"},
                    "port": {"type": "integer", "description": "新端口"},
                    "status": {"type": "string", "description": "active/inactive/unknown"},
                    "attributes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "key[:type]=value, 传了的属性覆盖, 未传的不变",
                    },
                },
                "required": ["name"],
            },
        },
        "cli_args": _update_resource_args,
    },
    "ops_delete_resource": {
        "schema": {
            "name": "ops_delete_resource",
            "description": "删除资源. 被关系引用时拒绝; 传 force=true 强制级联清理.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "资源 id 或 name"},
                    "force": {
                        "type": "boolean",
                        "description": "强制级联删除被引用的资源",
                    },
                },
                "required": ["name"],
            },
        },
        "cli_args": _delete_resource_args,
    },
    "ops_add_concern": {
        "schema": {
            "name": "ops_add_concern",
            "description": "为资源添加关注点 (如证书过期/磁盘水位). 支持设置到期时间和严重级别.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "资源 id 或 name"},
                    "category": {"type": "string", "description": "expiry/capacity/renewal/custom"},
                    "description": {
                        "type": "string",
                        "description": "描述, 如 SSL 证书 2026-12-31 到期",
                    },
                    "due": {"type": "string", "description": "到期时间 (ISO 8601)"},
                    "severity": {"type": "string", "description": "info/warning/critical"},
                },
                "required": ["resource", "category", "description"],
            },
        },
        "cli_args": _add_concern_args,
    },
    "ops_resolve_concern": {
        "schema": {
            "name": "ops_resolve_concern",
            "description": "将关注点标记为已解决. 修复问题后调用本工具闭环, 避免重复告警.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "关注点 id"},
                },
                "required": ["id"],
            },
        },
        "cli_args": _resolve_concern_args,
    },
}
