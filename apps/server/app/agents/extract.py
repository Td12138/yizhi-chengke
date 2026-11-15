"""萃取 Agent：版面块 → 知识点卡片（入库）。"""

from __future__ import annotations

from .. import db
from ..providers import llm
from .base import Agent


class ExtractAgent(Agent):
    name = "extract"

    async def run(self) -> list[dict]:
        await self.emit("running", "从版面块萃取知识点")
        blocks = self.ctx.state.get("blocks", [])
        result = await llm.chat_json("extract", {"blocks": blocks})
        cards = result.get("cards", []) or []

        source_id = int(self.ctx.state["source_id"])
        ids = db.insert_cards(source_id, cards)
        for card, card_id in zip(cards, ids):
            card["id"] = card_id

        self.ctx.state["cards"] = cards
        await self.emit("delta", f"生成 {len(cards)} 张知识点卡片", cards=cards)
        await self.emit("done", "卡片已入库，可被追问检索")
        return cards
