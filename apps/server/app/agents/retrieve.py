"""检索 Agent：追问时召回卡片原文，并强制标注出处。"""

from __future__ import annotations

from .. import config, db
from ..providers import llm
from .base import Agent


class RetrieveAgent(Agent):
    name = "retrieve"

    async def run(self) -> dict:
        query = str(self.ctx.state.get("query", "")).strip()
        await self.emit("running", f"检索卡片库：{query or '(空查询)'}")

        cards = db.search_cards(query, config.RAG_TOP_K)
        await self.emit(
            "delta",
            f"召回 {len(cards)} 张卡片",
            cards=[{"id": c["id"], "title": c["title"]} for c in cards],
        )

        result = await llm.chat_json("answer", {"query": query, "cards": cards})
        citations = result.get("citations") or [c["id"] for c in cards]
        answer = {
            "answer": result.get("answer", ""),
            "citations": citations,
            "cards": [
                {"id": c["id"], "title": c["title"], "summary": c["summary"]}
                for c in cards
            ],
        }
        await self.emit("done", "回答完成，已标注出处")
        return answer
