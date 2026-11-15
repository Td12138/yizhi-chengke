"""检索 Agent：追问时召回卡片原文，并强制标注出处。"""

from __future__ import annotations

from .. import config, db
from ..providers import llm
from .base import Agent


class RetrieveAgent(Agent):
    name = "retrieve"

    async def _bump_ask(self, cards: list[dict]) -> int | None:
        """命中卡片被问次数 +1；计数失败只记事件，不影响回答主流程。"""
        if not cards:
            return None
        try:
            db.bump_ask([c["id"] for c in cards])
        except Exception as exc:
            await self.emit(
                "delta", f"卡片计数失败（已忽略，不影响回答）：{type(exc).__name__}: {exc}"
            )
            return None
        return sum(int(c.get("ask_count") or 0) + 1 for c in cards)

    async def run(self) -> dict:
        query = str(self.ctx.state.get("query", "")).strip()
        await self.emit("running", f"检索卡片库：{query or '(空查询)'}")

        cards = db.search_cards(query, config.RAG_TOP_K)
        ask_total = await self._bump_ask(cards)
        message = f"召回 {len(cards)} 张卡片"
        if ask_total is not None:
            message += f"，累计被问 {ask_total} 次"
        await self.emit(
            "delta",
            message,
            cards=[{"id": c["id"], "title": c["title"]} for c in cards],
            ask_total=ask_total,
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
