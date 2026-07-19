"""内置资源类型.

设计原则: 每种类型只声明"是什么 + 怎么连"的必要字段, 具体功能操作以
``operations_guide`` 文本告诉 Agent, 不结构化进 schema. 任何额外字段由
用户在 CLI 上以扩展属性 (is_standard=false) 形式追加.

同时声明 ``default_concerns`` — 创建资源时自动生成的默认关注点.
"""

from __future__ import annotations

from .models import ConcernTemplate, Resource, register_resource


@register_resource
class AliyunAccount(Resource):
    """阿里云账号 (含访问凭据, 不对接 OpenAPI — Agent 自行调用 aliyun cli)."""

    type = "aliyun_account"
    standard_attributes = {
        "access_key_id": {"type": "str", "required": True, "description": "阿里云 AK"},
        "access_key_secret": {"type": "secret", "required": True, "description": "阿里云 SK"},
        "region": {"type": "str", "default": "cn-hangzhou", "description": "默认地域"},
    }
    operations_guide = (
        "访问: 配置 aliyun cli 的 AK/SK 后用 `aliyun <API>` 查询/操作. "
        "具体可用 API 由 Agent 自行决定, 不在资源元数据中描述."
    )
    default_concerns = [
        ConcernTemplate(
            category="security", description="AccessKey 定期轮换检查", severity="critical"
        ),
    ]


@register_resource
class ECSResource(Resource):
    """阿里云 ECS (或任意虚拟机/物理机)."""

    type = "ecs"
    standard_attributes = {
        "host": {"type": "str", "required": True, "description": "IP 或域名"},
        "ssh_port": {"type": "int", "default": "22", "description": "SSH 端口"},
        "ssh_user": {"type": "str", "required": True, "description": "SSH 登录用户"},
        "ssh_key_path": {"type": "str", "description": "SSH 私钥路径 (二选一)"},
        "ssh_password": {"type": "secret", "description": "SSH 密码 (二选一)"},
    }
    operations_guide = (
        "连接: `ssh -i <ssh_key_path> -p <ssh_port> <ssh_user>@<host>` "
        "或带密码方式登录. 登录后能做什么由 Agent 决定, 不在资源元数据中描述."
    )
    default_concerns = [
        ConcernTemplate(category="capacity", description="CPU 使用率监控", severity="warning"),
        ConcernTemplate(category="capacity", description="内存使用率监控", severity="warning"),
        ConcernTemplate(category="capacity", description="磁盘使用率监控", severity="warning"),
        ConcernTemplate(category="capacity", description="磁盘 IO 监控", severity="warning"),
        ConcernTemplate(category="capacity", description="网络带宽使用率监控", severity="warning"),
        ConcernTemplate(category="expiry", description="SSH 密钥到期检查", severity="critical"),
    ]


@register_resource
class RedisResource(Resource):
    type = "redis"
    standard_attributes = {
        "host": {"type": "str", "required": True},
        "port": {"type": "int", "default": "6379"},
        "db": {"type": "int", "default": "0"},
        "password": {"type": "secret"},
    }
    operations_guide = (
        "连接: `redis-cli -h <host> -p <port> -a <password> -n <db>`. 具体命令由 Agent 决定."
    )
    default_concerns = [
        ConcernTemplate(
            category="capacity",
            description="内存使用率监控 (used_memory vs maxmemory)",
            severity="warning",
        ),
        ConcernTemplate(
            category="capacity",
            description="缓存命中率监控 (keyspace_hits vs misses)",
            severity="warning",
        ),
        ConcernTemplate(
            category="capacity", description="key 逐出率监控 (evicted_keys > 0)", severity="warning"
        ),
        ConcernTemplate(
            category="capacity", description="客户端连接数监控 (connected_clients)", severity="info"
        ),
    ]


@register_resource
class MySQLResource(Resource):
    type = "mysql"
    standard_attributes = {
        "host": {"type": "str", "required": True},
        "port": {"type": "int", "default": "3306"},
        "db_name": {"type": "str", "required": True},
        "username": {"type": "str", "required": True},
        "password": {"type": "secret"},
    }
    operations_guide = (
        "连接: `mysql -h <host> -P <port> -u <username> -p<password> <db_name>`. "
        "具体 SQL 由 Agent 决定."
    )
    default_concerns = [
        ConcernTemplate(
            category="capacity",
            description="连接数使用率 > 80% (max_connections)",
            severity="warning",
        ),
        ConcernTemplate(category="renewal", description="备份状态检查", severity="critical"),
        ConcernTemplate(
            category="capacity", description="数据库磁盘使用率监控", severity="warning"
        ),
    ]


@register_resource
class PostgresResource(Resource):
    type = "postgres"
    standard_attributes = {
        "host": {"type": "str", "required": True},
        "port": {"type": "int", "default": "5432"},
        "db_name": {"type": "str", "required": True},
        "username": {"type": "str", "required": True},
        "password": {"type": "secret"},
    }
    operations_guide = (
        "连接: `PGPASSWORD=<password> psql -h <host> -p <port> -U <username> -d <db_name>`. "
        "具体 SQL 功能由 Agent 自行决定, 不在资源元数据中描述."
    )
    default_concerns = [
        ConcernTemplate(
            category="capacity",
            description="连接数使用率 > 80% (max_connections)",
            severity="warning",
        ),
        ConcernTemplate(
            category="capacity", description="复制延迟监控 (replay_lag)", severity="warning"
        ),
        ConcernTemplate(
            category="renewal", description="备份状态检查 (归档状态)", severity="critical"
        ),
        ConcernTemplate(
            category="capacity", description="数据库磁盘使用率监控 (含 pg_wal)", severity="warning"
        ),
    ]


@register_resource
class HBaseResource(Resource):
    type = "hbase"
    standard_attributes = {
        "host": {"type": "str", "required": True, "description": "Thrift/ZK 主机"},
        "port": {"type": "int", "default": "9090", "description": "Thrift 端口"},
        "zk_quorum": {"type": "str", "description": "ZooKeeper quorum (逗号分隔)"},
    }
    operations_guide = "连接: 通过 hbase shell 或 Thrift 客户端访问. 具体操作由 Agent 决定."
    default_concerns = [
        ConcernTemplate(
            category="capacity", description="RegionServer 健康检查", severity="warning"
        ),
        ConcernTemplate(category="capacity", description="HDFS 磁盘使用率监控", severity="warning"),
        ConcernTemplate(category="capacity", description="Region 均衡状态检查", severity="info"),
    ]


@register_resource
class ServiceResource(Resource):
    """自部署的应用服务 (Java/Python 等, 用 runtime 标准字段区分语言)."""

    type = "service"
    standard_attributes = {
        "endpoint": {"type": "str", "description": "部署位置 (域名/IP/宿主机)"},
        "port": {"type": "int", "description": "服务端口"},
        "runtime": {"type": "str", "required": True, "description": "运行时: java|python|..."},
        "deploy_path": {"type": "str", "description": "部署路径"},
        "start_cmd": {"type": "str", "description": "启动命令"},
        "stop_cmd": {"type": "str", "description": "停止命令"},
        "health_check_url": {"type": "str", "description": "健康检查 URL"},
        "log_path": {"type": "str", "description": "日志路径"},
    }
    operations_guide = (
        "运维: 通过 start_cmd/stop_cmd 控制进程, health_check_url 探活, "
        "log_path 排查日志. 具体动作 (如重启, 滚动发布) 由 Agent 决定."
    )
    default_concerns = [
        ConcernTemplate(category="health", description="健康检查探活", severity="critical"),
        ConcernTemplate(category="capacity", description="日志磁盘使用监控", severity="warning"),
    ]


@register_resource
class APISIXResource(Resource):
    """APISIX 网关 (通过 Admin API 管理)."""

    type = "apisix"
    standard_attributes = {
        "admin_endpoint": {"type": "str", "required": True, "description": "Admin API 地址"},
        "admin_key": {"type": "secret", "required": True, "description": "Admin API 密钥"},
        "admin_port": {"type": "int", "default": "9180", "description": "Admin API 端口"},
    }
    operations_guide = (
        "管理: 通过 Admin API 操作: curl http://<admin_endpoint>:<admin_port>/apisix/admin/ . "
        "具体操作 (路由管理/上游配置/插件开关) 由 Agent 决定."
    )
    default_concerns = [
        ConcernTemplate(category="expiry", description="SSL 证书到期检查", severity="critical"),
        ConcernTemplate(category="health", description="Admin API 健康检查", severity="warning"),
        ConcernTemplate(category="health", description="上游节点健康检查", severity="warning"),
    ]


@register_resource
class KeycloakResource(Resource):
    """Keycloak 认证服务."""

    type = "keycloak"
    standard_attributes = {
        "endpoint": {"type": "str", "required": True, "description": "Keycloak 服务器地址"},
        "port": {"type": "int", "default": "8080", "description": "服务端口"},
        "realm": {"type": "str", "required": True, "description": "默认 Realm"},
        "admin_user": {"type": "str", "required": True, "description": "管理员用户名"},
        "admin_password": {"type": "secret", "required": True, "description": "管理员密码"},
    }
    operations_guide = (
        "管理: 通过 Keycloak Admin REST API 操作. 具体 (Realm 配置/Client 管理/"
        "用户同步) 由 Agent 自行决定."
    )
    default_concerns = [
        ConcernTemplate(category="expiry", description="SSL 证书到期检查", severity="critical"),
        ConcernTemplate(category="health", description="Keycloak 服务健康检查", severity="warning"),
        ConcernTemplate(category="health", description="Token 签发状态检查", severity="warning"),
    ]


@register_resource
class EtcdResource(Resource):
    """etcd 分布式键值存储 (APISIX 配置存储)."""

    type = "etcd"
    standard_attributes = {
        "endpoints": {"type": "str", "required": True, "description": "逗号分隔的 etcd 端点"},
        "port": {"type": "int", "default": "2379", "description": "客户端端口"},
        "ca_cert": {"type": "str", "description": "CA 证书路径"},
        "client_cert": {"type": "str", "description": "客户端证书路径"},
        "client_key": {"type": "secret", "description": "客户端密钥路径"},
    }
    operations_guide = (
        "连接: `etcdctl --endpoints=<endpoints>`. 具体操作 (键值管理/集群状态/"
        "租约管理) 由 Agent 决定."
    )
    default_concerns = [
        ConcernTemplate(category="health", description="etcd 集群健康检查", severity="critical"),
        ConcernTemplate(
            category="capacity", description="etcd 存储配额使用率监控", severity="warning"
        ),
        ConcernTemplate(category="health", description="etcd Leader 变更监控", severity="warning"),
    ]
