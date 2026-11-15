"""编排器：一条漏斗的调度 + 质检回环 + 事件发布。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from . import config, db
from .agents.base import Agent, RunContext
from .agents.compose import ComposeAgent
from .agents.explain import ExplainAgent
from .agents.extract import ExtractAgent
from .agents.recognize import RecognizeAgent
from .agents.retrieve import RetrieveAgent
from .agents.verify import VerifyAgent
from .events import Event, EventBus


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# 中文输入法常见的全角标点，判分前统一成半角（逗号要保留，不能直接删）
FULLWIDTH_TABLE = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "，": ",",
        "：": ":",
        "；": ";",
        "。": ".",
        "！": "!",
        "？": "?",
        "【": "[",
        "】": "]",
    }
)


def normalize(text: str) -> str:
    """判分归一化：全角标点转半角，去掉空白、$、括号、反斜杠并统一小写。"""
    text = str(text).translate(FULLWIDTH_TABLE)
    return re.sub(r"[\s$\\({})\\\\]", "", text).lower()


def judge(question: dict, user_answer: str) -> bool:
    expected = normalize(question.get("answer", ""))
    given = normalize(user_answer)
    if question.get("qtype") == "choice":
        return bool(given) and bool(expected) and given[0] == expected[0]
    return given == expected


class Orchestrator:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def _emit(
        self, run_id: str, agent: str, phase: str, message: str = "", **payload: Any
    ) -> None:
        await self.bus.publish(Event(run_id, agent, phase, message, payload))

    async def _step(self, ctx: RunContext, agent_cls: type[Agent]) -> Any:
        agent = agent_cls(ctx)
        await self._emit(ctx.run_id, agent.name, "queued", f"{agent.label} Agent 排队")
        return await agent.run()

    async def _fail(self, run_id: str, exc: Exception) -> None:
        await self._emit(
            run_id, "orchestrator", "error", f"{type(exc).__name__}: {exc}"
        )

    async def ingest(self, run_id: str, image_path: Path) -> None:
        """拍照 → 版面块 → 知识点卡片 → 入库。"""
        ctx = RunContext(run_id, self.bus, {"image_path": str(image_path)})
        try:
            blocks = await self._step(ctx, RecognizeAgent)
            ctx.state["source_id"] = db.insert_source(str(image_path), blocks)
            cards = await self._step(ctx, ExtractAgent)
            await self._emit(
                run_id,
                "orchestrator",
                "done",
                f"入库完成：{len(cards)} 张卡片",
                result={"cards": cards, "source_id": ctx.state["source_id"]},
            )
        except Exception as exc:
            await self._fail(run_id, exc)
        finally:
            self.bus.close(run_id)

    async def generate(
        self, run_id: str, card_ids: list[int], requirement: str, count: int
    ) -> None:
        """一句话需求 → 命题 ↔ 质检回环 → 交付。"""
        cards = db.get_cards(card_ids) if card_ids else db.list_cards(limit=3)
        ctx = RunContext(
            run_id,
            self.bus,
            {"cards": cards, "requirement": requirement, "count": count},
        )
        max_rounds = config.VERIFY_MAX_RETRY + 1
        try:
            for round_no in range(1, max_rounds + 1):
                ctx.state["round"] = round_no
                await self._step(ctx, ComposeAgent)
                results = await self._step(ctx, VerifyAgent)
                if results and all(results):
                    break
                if round_no < max_rounds:
                    await self._emit(
                        run_id,
                        "orchestrator",
                        "delta",
                        f"第 {round_no} 轮有题未通过，打回命题 Agent 重生成",
                    )

            questions = ctx.state.get("questions", [])
            delivered = [q for q in questions if q.get("verified")]
            dropped = len(questions) - len(delivered)
            if not delivered:
                raise RuntimeError("全部题目未通过质检，拒绝交付未验证内容")

            ids = db.insert_questions(delivered)
            for question, question_id in zip(delivered, ids):
                question["id"] = question_id

            message = f"交付 {len(delivered)} 道题"
            if dropped:
                message += f"，丢弃 {dropped} 道未通过质检"
            await self._emit(
                run_id,
                "orchestrator",
                "done",
                message,
                result={"questions": delivered, "dropped": dropped},
            )
        except Exception as exc:
            await self._fail(run_id, exc)
        finally:
            self.bus.close(run_id)

    async def grade(self, run_id: str, question_id: int, user_answer: str) -> None:
        """作答判分；答错则触发讲解 Agent。"""
        ctx = RunContext(run_id, self.bus, {})
        try:
            question = db.get_question(question_id)
            if question is None:
                raise ValueError(f"题目 {question_id} 不存在")

            is_correct = judge(question, user_answer)
            db.insert_attempt(question_id, user_answer, is_correct)
            ctx.state.update({"question": question, "user_answer": user_answer})

            if is_correct:
                await self._emit(run_id, "explain", "done", "答案正确，跳过讲解")
                await self._emit(
                    run_id,
                    "orchestrator",
                    "done",
                    "判分完成：正确",
                    result={"correct": True},
                )
            else:
                explain = await self._step(ctx, ExplainAgent)
                await self._emit(
                    run_id,
                    "orchestrator",
                    "done",
                    "判分完成：错误，已生成讲解",
                    result={"correct": False, "explain": explain},
                )
        except Exception as exc:
            await self._fail(run_id, exc)
        finally:
            self.bus.close(run_id)

    async def ask(self, run_id: str, query: str) -> None:
        """追问：检索卡片原文并作答。"""
        ctx = RunContext(run_id, self.bus, {"query": query})
        try:
            answer = await self._step(ctx, RetrieveAgent)
            await self._emit(run_id, "orchestrator", "done", "回答完成", result=answer)
        except Exception as exc:
            await self._fail(run_id, exc)
        finally:
            self.bus.close(run_id)
