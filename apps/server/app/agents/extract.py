"""萃取 Agent：版面块 → 知识点卡片（一张卡片一条 delta，入库后回填 id）。"""

from __future__ import annotations

import asyncio

from .. import config, db
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

        # 一张卡片一条 delta：id 已经回填，前端点开就能追问这张卡
        for index, card in enumerate(cards, start=1):
            await self.emit(
                "delta",
                f"第 {index}/{len(cards)} 张：{card.get('title', '未命名知识点')}",
                card=card,
                index=index,
                total=len(cards),
            )
            if index < len(cards):
                await asyncio.sleep(config.DELTA_PACE_SECONDS)

        await self.emit("done", "卡片已入库，可被追问检索", cards=cards)
        return cards
