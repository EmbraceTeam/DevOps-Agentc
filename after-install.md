# opsctl-plugin 安装完成

插件通过 subprocess 调用仓库内 CLI shim (`bin/opsctl_shim.py`), 需要以下一次性步骤:

## 1. 安装 CLI 依赖 (Hermes venv)

```bash
$HERMES_VENV/bin/pip install typer rich
```

> `$HERMES_VENV` 是 Hermes 的虚拟环境路径 (如 `$HOME/.hermes/hermes-agent/venv`)。

## 2. 启用插件

```bash
hermes plugins enable opsctl-plugin
```

## 3. 重启 Hermes 生效

```bash
hermes gateway restart
```

## 更新

```bash
hermes plugins update opsctl-plugin
```

CLI 与插件代码随仓库一起更新, 一条命令搞定。

## 验证

```bash
hermes plugins list          # 应显示 opsctl-plugin | enabled
hermes chat -q "使用opsctl查看资源类型"
```
