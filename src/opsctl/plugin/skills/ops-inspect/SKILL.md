# ops-inspect — 统一运维巡检

遍历每个已登记资源，逐个检查其 open 关注项，汇总报告。

## 流程

1. **列出全部资源** — 调用 `ops_list_resources` 获取清单
2. **遍历每个资源** — 对每个资源调用 `ops_list_concerns --resource <name>` 查看其 open 关注项
3. **分级报告** — 按 severity 排序输出 (critical → warning → info)
4. **汇总统计** — 最后给出总数和分类统计

## 报告格式

```text
## 统一巡检结果 (YYYY-MM-DD HH:MM UTC)

**资源总数**: N

### 🔴 Critical
- [资源名] — [描述] — 到期: [due_at]
### 🟡 Warning
- [资源名] — [描述] — 到期: [due_at]
### 🔵 Info
- [资源名] — [描述] — 到期: [due_at]

**统计**: critical=N, warning=N, info=N, total=N
```

## 示例

资源列表: web-prod-1 (ecs), pg-main (postgres), redis-cache (redis)

执行:

1. `ops_list_concerns --resource web-prod-1` → [open: SSL 证书到期]
2. `ops_list_concerns --resource pg-main` → [open: 磁盘水位 80%]
3. `ops_list_concerns --resource redis-cache` → [空]

报告:

```text
### 🔴 Critical
- web-prod-1 — SSL 证书到期 — 到期: 2026-08-01

### 🟡 Warning
- pg-main — 磁盘水位 80% — 到期: 2026-08-15
```
