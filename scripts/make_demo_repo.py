"""生成演示 Git 仓库：刻意使用低质量 commit message（题目痛点），diff 有真实语义。

用法：python scripts/make_demo_repo.py [目标目录]
"""
from __future__ import annotations

import os
import subprocess
import sys

DEMO_FILES = [
    # (message, {path: content})  —— message 全是"修复问题"式的无效描述
    ("修改接口", {
        "src/main/java/com/demo/order/controller/OrderExportController.java": (
            "package com.demo.order.controller;\n\n"
            "import org.springframework.security.access.prepost.PreAuthorize;\n"
            "import org.springframework.web.bind.annotation.*;\n\n"
            "@RestController\n@RequestMapping(\"/api/order/export\")\n"
            "public class OrderExportController {\n\n"
            "    @PreAuthorize(\"hasRole('ADMIN')\")\n"
            "    @PostMapping\n"
            "    public Result export(@RequestBody OrderExportReq req) {\n"
            "        // 大导出走异步任务，小导出直接同步返回\n"
            "        if (req.getRowLimit() > 10000) {\n"
            "            return asyncExportService.submit(req);\n"
            "        }\n"
            "        return Result.ok(exportService.exportSync(req));\n"
            "    }\n"
            "}\n"
        ),
    }),
    ("修复问题", {
        "src/main/java/com/demo/order/service/OrderService.java": (
            "package com.demo.order.service;\n\n"
            "public class OrderService {\n\n"
            "    public Order findOrder(String orderNo) {\n"
            "        // 修复: orderNo 为空串时误查全表，直接返回 null 由上层兜底\n"
            "        if (orderNo == null || orderNo.isEmpty()) {\n"
            "            return null;\n"
            "        }\n"
            "        return orderRepository.findByNo(orderNo);\n"
            "    }\n"
            "}\n"
        ),
    }),
    ("优化逻辑", {
        "src/main/java/com/demo/order/service/OrderQueryService.java": (
            "package com.demo.order.service;\n\n"
            "public class OrderQueryService {\n\n"
            "    // 优化: 热点订单查询加本地缓存，TTL 5 分钟，命中率约 80%\n"
            "    private final LoadingCache<String, Order> cache = CacheBuilder.newBuilder()\n"
            "            .expireAfterWrite(5, TimeUnit.MINUTES)\n"
            "            .maximumSize(10000)\n"
            "            .build(this::loadOrder);\n\n"
            "    public Order getOrder(String orderNo) {\n"
            "        return cache.getUnchecked(orderNo);\n"
            "    }\n"
            "}\n"
        ),
    }),
    ("配置调整", {
        "src/main/resources/application.yml": (
            "server:\n  port: 8080\n\n"
            "order:\n"
            "  export:\n"
            "    # 导出线程池参数调整: 导出高峰期队列打满导致 OOM\n"
            "    core-pool-size: 8\n"
            "    max-pool-size: 16\n"
            "    queue-capacity: 500\n"
            "  query:\n"
            "    cache-ttl-seconds: 300\n"
        ),
    }),
    ("数据库变更", {
        "src/main/resources/db/migration/V12__order_add_export_fields.sql": (
            "-- 订单导出功能补充字段: 导出时间与导出状态，配合导出中心使用\n"
            "ALTER TABLE t_order ADD COLUMN exported_at DATETIME NULL;\n"
            "ALTER TABLE t_order ADD COLUMN export_status TINYINT DEFAULT 0 NOT NULL;\n"
            "CREATE INDEX idx_order_export_status ON t_order (export_status);\n"
        ),
    }),
]


def main(target: str) -> None:
    if os.path.exists(os.path.join(target, ".git")):
        print(f"演示仓库已存在，跳过: {target}")
        return
    os.makedirs(target, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "demo-dev", "GIT_AUTHOR_EMAIL": "demo@demo.com",
           "GIT_COMMITTER_NAME": "demo-dev", "GIT_COMMITTER_EMAIL": "demo@demo.com"}

    def git(*args, **kw):
        subprocess.run(["git", *args], cwd=target, check=True, env=env, capture_output=True)

    git("init", "-q", "-b", "main")
    for i, (message, files) in enumerate(DEMO_FILES, 1):
        for path, content in files.items():
            full = os.path.join(target, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
        git("add", "-A")
        git("commit", "-q", "-m", message)
        print(f"  commit {i}: {message}")
    print(f"演示仓库就绪: {target}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "demo_repo")
    main(out)
