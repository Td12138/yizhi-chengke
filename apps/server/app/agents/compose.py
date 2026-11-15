"""命题 Agent：知识点卡片 + 自然语言需求 → 变式题（一题一条 delta）。"""

from __future__ import annotations

import asyncio

from .. import config
from ..providers import llm
from .base import Agent
from .verify import brief


class ComposeAgent(Agent):
    name = "compose"

    async def run(self) -> list[dict]:
        round_no = int(self.ctx.state.get("round", 1))
        requirement = self.ctx.state.get("requirement", "出几道基础题")
        count = int(self.ctx.state.get("count", config.DEFAULT_QUESTION_COUNT))
        cards = self.ctx.state.get("cards", [])

        await self.emit("running", f"按需求命题（第 {round_no} 轮）：{requirement}")
        result = await llm.chat_json(
            "compose",
            {
                "requirement": requirement,
                "count": count,
                "round": round_no,
                "cards": cards,
            },
        )
        questions = (result.get("questions") or [])[:count]

        card_ids = [c["id"] for c in cards if "id" in c]
        for question in questions:
            question.setdefault("card_ids", card_ids)

        self.ctx.state["questions"] = questions

        # 一道题一条 delta：前端可以逐题渲染，而不是等整批出完再一次性刷出来
        for index, question in enumerate(questions, start=1):
            await self.emit(
                "delta",
                f"第 {index}/{len(questions)} 题：{brief(question.get('stem_latex', ''))}",
                question=question,
                index=index,
                total=len(questions),
            )
            if index < len(questions):
                await asyncio.sleep(config.DELTA_PACE_SECONDS)

        await self.emit("done", "命题完成，交给质检 Agent", questions=questions)
        return questions
