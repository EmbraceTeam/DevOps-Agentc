#!/usr/bin/env python3
"""opsctl CLI shim — 从插件仓库内加载 CLI 实现.

目录插件模式下, Hermes 插件 handler 通过 subprocess 以本文件为入口调用
opsctl CLI (见 tools.py 的 _opsctl_binary). CLI 源码随插件仓库 git 更新,
无需单独 pip 安装.

依赖 (typer/rich) 需安装在运行本 shim 的 Python 环境 (Hermes venv).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from opsctl.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
