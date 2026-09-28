"""FastAPI 入口：M1 最小闭环 API。

启动：uvicorn app.main:app --port 8100
文档：http://127.0.0.1:8100/docs
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="commit-knowledge-platform",
    description="MT30 课题：基于代码提交历史的研发知识沉淀与智能代码辅助生成工具（M1 最小闭环）",
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
