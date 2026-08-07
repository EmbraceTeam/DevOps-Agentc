"""Hermes 目录插件入口 — 转发到 src/opsctl/plugin 实现.

目录插件模式: ``hermes plugins install <git-url>`` 克隆本仓库到
``~/.hermes/plugins/opsctl-plugin/``, Hermes 加载本文件后调用 ``register(ctx)``.

CLI 实现位于 ``src/opsctl``, 由 ``bin/opsctl_shim.py`` 提供 subprocess 入口
(插件 handler 与 CLI 保持 subprocess 解耦, 见 spec Design Notes).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_plugin = importlib.import_module("opsctl.plugin")
register = _plugin.register
