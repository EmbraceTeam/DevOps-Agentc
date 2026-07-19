"""opsctl Plugin 工具 handler: 通过 subprocess 调 opsctl CLI --json.

不直接 import repository, 保持 Plugin ↔ CLI 解耦 (见 spec Design Notes).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable


def _opsctl_binary() -> str:
    """定位 opsctl 可执行文件, 允许环境变量覆盖."""
    import os

    override = os.environ.get("OPSCTL_BINARY")
    if override:
        return override
    found = shutil.which("opsctl")
    if found:
        return found
    # 退回模块调用, 单测可 patch 本函数
    return "opsctl"


def _run_opsctl(argv: list[str]) -> dict:
    """运行 opsctl 子命令并解析 JSON 输出.

    任何进程级异常或非零退出都包装成 ``{"error": "..."}`` 返回给 LLM.
    """
    binary = _opsctl_binary()
    cmd = [binary, *argv]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"调用 opsctl 失败: {exc}", "cmd": cmd}
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        # CLI 的 --json 错误路径本身就输出 {"error": "..."}, 优先解析避免双层包装
        parsed: dict | None = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict) and "error" in parsed:
            return {"error": parsed["error"], "returncode": proc.returncode}
        return {
            "error": stderr or stdout or f"opsctl 退出码 {proc.returncode}",
            "returncode": proc.returncode,
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout} if proc.stdout else {"error": "opsctl 无输出"}


def make_handler(cli_args_fn: Callable[[dict], list[str]]):  # type: ignore[no-untyped-def]
    """根据 cli_args 函数生成工具 handler."""

    def handler(params: dict, **_kwargs) -> str:
        try:
            argv = cli_args_fn(params or {})
            result = _run_opsctl(argv)
        except (KeyError, TypeError, ValueError) as exc:
            # 参数构造或调用异常也要返回 JSON, 守 spec I/O "CLI 异常→{error}"
            result = {"error": f"参数构造失败: {exc}"}
        return json.dumps(result, ensure_ascii=False)

    return handler
