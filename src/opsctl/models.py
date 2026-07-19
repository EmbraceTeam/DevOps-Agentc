"""资源抽象与注册表.

设计原则（见 spec frozen 块）:
- 资源统一抽象: 所有资源都是 ``Resource`` 子类, 遵循"固定字段 + 扩展字段"模式.
- 资源只存"是什么 + 怎么连", 不存"怎么用". 具体操作能力以纯文本
  ``operations_guide`` 告诉 Agent, 不结构化进 schema.
- 新增类型只需在任意模块定义子类并 ``@register_resource``, 无需改动其它文件.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

# value_type 取值, 与 resource_attributes.value_type 列对应
VALID_VALUE_TYPES = frozenset({"str", "int", "bool", "secret", "json"})


class ResourceRegistryError(Exception):
    """资源类型注册相关错误."""


class ResourceTypeNotFoundError(ResourceRegistryError):
    """请求的资源类型未注册."""


class DuplicateResourceTypeError(ResourceRegistryError):
    """重复注册同 type 的资源类."""


# 全局类型注册表: type 字符串 -> Resource 子类
_REGISTRY: dict[str, type[Resource]] = {}


def register_resource(cls: type[Resource]) -> type[Resource]:
    """类装饰器: 把 Resource 子类登记进全局注册表.

    以子类的 ``type`` 属性作为键. 重复注册同一 type 视为冲突, 抛出
    ``DuplicateResourceTypeError`` 防止静默覆盖.
    """
    type_name = getattr(cls, "type", None)
    if not isinstance(type_name, str) or not type_name:
        raise ResourceRegistryError(f"{cls.__name__} 缺少有效的 type 字符串属性")
    if type_name in _REGISTRY and _REGISTRY[type_name] is not cls:
        raise DuplicateResourceTypeError(
            f"资源类型 '{type_name}' 已被 {_REGISTRY[type_name].__name__} 注册"
        )
    _REGISTRY[type_name] = cls
    return cls


def get_resource_class(type_name: str) -> type[Resource]:
    """按类型名取出 Resource 子类; 未注册则抛 ``ResourceTypeNotFoundError``."""
    try:
        return _REGISTRY[type_name]
    except KeyError as exc:
        raise ResourceTypeNotFoundError(
            f"未注册的资源类型 '{type_name}'. 已知: {sorted(_REGISTRY)}"
        ) from exc


def list_resource_types() -> list[str]:
    """返回所有已注册类型名 (排序后)."""
    return sorted(_REGISTRY)


@dataclass
class AttributeSpec:
    """单个标准属性的声明."""

    type: str = "str"
    required: bool = False
    default: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in VALID_VALUE_TYPES:
            raise ResourceRegistryError(
                f"非法 value_type '{self.type}', 允许: {sorted(VALID_VALUE_TYPES)}"
            )


@dataclass
class ConcernTemplate:
    """资源类型的默认关注点模板.

    创建资源时 ``create_resource`` 会自动为每个模板生成一条 concern.
    ``due_at`` 不应包含在模板中 — 那是具体资源实例化的运行时值.
    """

    category: str
    description: str
    severity: str = "info"


class Resource:
    """资源抽象基类.

    子类需声明:
    - ``type``: 类型字符串 (唯一键, 如 "ecs", "postgres")
    - ``standard_attributes``: 该类型的固定字段声明 dict
    - ``operations_guide``: 纯文本, 告诉 Agent 如何操作该类型资源
    - (可选) ``default_concerns``: 创建资源时自动生成的默认关注点

    ``standard_attributes`` 形如::

        {
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "default": 5432},
            "password": {"type": "secret"},
        }

    ``default_concerns`` 形如::

        [
            ConcernTemplate(category="expiry", description="SSL 证书到期检查", severity="critical"),
            ConcernTemplate(category="capacity", description="磁盘使用率监控", severity="warning"),
        ]
    """

    type: ClassVar[str] = ""
    standard_attributes: ClassVar[dict[str, dict]] = {}
    operations_guide: ClassVar[str] = ""
    default_concerns: ClassVar[list[ConcernTemplate]] = []

    @classmethod
    def resolved_standard_attributes(cls) -> dict[str, AttributeSpec]:
        """把声明 dict 转成 AttributeSpec, 触发类型校验."""
        return {name: AttributeSpec(**spec) for name, spec in cls.standard_attributes.items()}

    @classmethod
    def required_attribute_names(cls) -> list[str]:
        return [name for name, spec in cls.resolved_standard_attributes().items() if spec.required]


@dataclass
class Relation:
    """资源依赖关系 (只管依赖, 无分组无标签)."""

    id: int | None
    source_id: str
    target_id: str
    relation_type: str = "depends_on"
    note: str = ""
    created_at: str = ""


@dataclass
class Concern:
    """资源关注点 (如证书过期, 配合 Hermes Cron)."""

    id: int | None
    resource_id: str
    category: str
    description: str
    due_at: str | None = None
    severity: str = "info"  # info | warning | critical
    checked_at: str | None = None
    status: str = "open"  # open | resolved | snoozed


class CycleError(Exception):
    """添加关系会形成循环依赖.

    ``cycle`` 属性保存循环路径上的资源 id 序列, 便于上层向用户报告.
    """

    def __init__(self, cycle: list[str]) -> None:
        super().__init__("拒绝创建: 会形成循环依赖 " + " -> ".join(cycle))
        self.cycle = cycle
