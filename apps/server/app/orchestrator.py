"""编排器：一条漏斗的调度 + 质检回环 + 事件发布。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from . import config, db, documents, storage
from .agents.base import Agent, RunContext
from .agents.compose import ComposeAgent
from .agents.explain import ExplainAgent
from .agents.extract import ExtractAgent
from .agents.recognize import RecognizeAgent
from .agents.retrieve import RetrieveAgent
from .agents.review import ReviewAgent
from .agents.verify import VerifyAgent
from .events import Event, EventBus
# 判分规则集中在 judge 模块（纯函数），此处 re-export 保持既有调用方不变
from .judge import judge, normalize  # noqa: F401


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


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

    async def _bump_question(self, run_id: str, questions: list[dict]) -> None:
        """交付题目用到的卡片计数 +1；失败只记事件，不影响交付。"""
        card_ids: list[int] = []
        for question in questions:
            card_ids.extend(question.get("card_ids") or [])
        if not card_ids:
            return
        try:
            db.bump_question(card_ids)
        except Exception as exc:
            await self._emit(
                run_id,
                "orchestrator",
                "delta",
                f"卡片计数失败（已忽略，不影响交付）：{type(exc).__name__}: {exc}",
            )

    async def _read_input(self, run_id: str, ctx: RunContext, path: Path) -> list[dict]:
        """图片走视觉模型；文档走 documents（PDF 内部还有「渲染成图」兜底）。"""
        if path.suffix.lower() not in config.DOC_SUFFIXES:
            return await self._step(ctx, RecognizeAgent)

        await self._emit(run_id, "recognize", "running", f"读取文档：{path.name}")
        blocks, note = await documents.read_document(path)
        ctx.state["blocks"] = blocks
        # 事件里说清走的哪条路径：文本层直读 / 渲染前 N 页
        await self._emit(
            run_id, "recognize", "delta", note, blocks_count=len(blocks)
        )
        await self._emit(
            run_id, "recognize", "done", f"文档解析完成：{len(blocks)} 个版面块"
        )
        return blocks

    async def _archive_input(
        self,
        run_id: str,
        source_id: int,
        file_path: Path,
        blocks: list[dict],
        cards: list[dict],
    ) -> Path:
        """识别 + 萃取完成后按标题改名归档，并回写 source 表。

        改名失败只发事件、保留临时名，不影响卡片入库与原文件可读。
        扩展名沿用原文件（图片还是图片，PDF 还是 PDF）。
        """
        title = storage.pick_title(blocks, cards)
        try:
            archived = storage.rename_after_ocr(file_path, title)
        except OSError as exc:
            await self._emit(
                run_id,
                "orchestrator",
                "delta",
                f"原文件重命名失败（保留原名）：{type(exc).__name__}: {exc}",
            )
            archived = file_path

        try:
            db.update_source_image(
                source_id,
                str(archived),
                archived.name,
                time.time() if archived != file_path else None,
            )
        except Exception as exc:  # 归档信息写不进去也不能影响已入库的卡片
            await self._emit(
                run_id,
                "orchestrator",
                "delta",
                f"原文件归档信息写入失败（已忽略）：{type(exc).__name__}: {exc}",
            )
            return archived

        if archived != file_path:
            await self._emit(
                run_id, "orchestrator", "delta", f"原文件已归档：{archived.name}"
            )
        return archived

    async def ingest(self, run_id: str, file_path: Path) -> None:
        """拍照 / 上传文档 → 版面块 → 知识点卡片 → 原文件归档 → 入库。"""
        ctx = RunContext(run_id, self.bus, {"image_path": str(file_path)})
        try:
            blocks = await self._read_input(run_id, ctx, file_path)
            ctx.state["source_id"] = db.insert_source(str(file_path), blocks)
            cards = await self._step(ctx, ExtractAgent)
            archived = await self._archive_input(
                run_id, ctx.state["source_id"], file_path, blocks, cards
            )
            await self._emit(
                run_id,
                "orchestrator",
                "done",
                f"入库完成：{len(cards)} 张卡片",
                result={
                    "cards": cards,
                    "source_id": ctx.state["source_id"],
                    "image_path": str(archived),
                },
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

            await self._bump_question(run_id, delivered)

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

    async def review(self, run_id: str, question_id: int, user_answer: str) -> None:
        """AI 批改：按得分点给分 + 流式评语，结构化结果随 done 事件交付。"""
        ctx = RunContext(run_id, self.bus, {})
        try:
            question = db.get_question(question_id)
            if question is None:
                raise ValueError(f"题目 {question_id} 不存在")
            ctx.state.update({"question": question, "user_answer": user_answer})

            review = await self._step(ctx, ReviewAgent)

            # 作答留痕：统计页与「错题回顾」都读 attempt 表
            db.insert_attempt(
                question_id,
                user_answer,
                review["verdict"] == "correct",
                "；".join(review.get("issues") or []),
            )

            label = {"correct": "正确", "partial": "部分正确", "wrong": "错误"}[
                review["verdict"]
            ]
            await self._emit(
                run_id,
                "orchestrator",
                "done",
                f"批改完成：{review['score']} 分（{label}）",
                result=review,
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
