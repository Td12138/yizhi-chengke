"""知识库统计接口：谱系图与热度榜。独立模块，main 只负责挂载路由。"""

from __future__ import annotations

from fastapi import APIRouter

from . import db

router = APIRouter()


@router.get("/api/graph")
async def graph() -> dict:
    """知识谱系：节点=卡片，边=共享非泛化标签（无可用标签时按来源串链）。"""
    return db.graph_data()


@router.get("/api/stats")
async def stats() -> dict:
    """热度榜：被问次数最多的前 N 张卡片。"""
    return {"top_asked": db.top_asked_cards(db.STATS_TOP_N)}
