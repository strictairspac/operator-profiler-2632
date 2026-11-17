# gb-4105 · 批量换号验收记录

源码不包含测试产物；本目录保存本批验收用的测试脚本与截图。

## 后端
`test_renumber.py` / `test_concurrency.py` 针对真实 PostgreSQL 运行（本地临时集群，非项目 compose）：

```sh
DATABASE_URL='postgresql+psycopg://trace@/operatortrace?host=/tmp&port=17744' \
REDIS_URL='redis://127.0.0.1:6399/0' JWT_SECRET=test PYTHONPATH=backend \
python -m pytest -q test-artifacts/gb-4105/test_renumber.py test-artifacts/gb-4105/test_concurrency.py
```

覆盖：互换、三元素循环、目标撞号、来源不存在、重复目标、重复传感器倍数保留、数据集编辑过期、新增计划过期、计划编辑过期、范围条件保序迁移与整批拒绝、执行结果等价（checksum 相同）、并发确认幂等、失败回滚后可重试、旧快照不变、确认期间新受理执行取得同一版数据与计划、观察员 403、审计与 OpenAPI。

结果：22 passed。

## 前端
`ui.mjs`（预演→插入新计划→过期→重做→确认）、`flow2.mjs`（循环换号、过期提示、观察员只读）、`pill3.mjs`（预演卡片特写）用 Playwright 驱动 Chromium，对 `/workspace/frontend` 静态目录 + 后端代理运行。

截图：`preview.png`（预演）、`confirmed.png`（完成）、`stale.png`（过期提示与历史「预演已过期」）、`pill2.png`（预演卡片）、`mobile.png` / `tablet.png`（390px / 820px 无横向溢出）。
