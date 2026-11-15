"""质检三态：验算通过 / 验算驳回 / 没有 verify 字段（只做结构检查）。"""

from __future__ import annotations

import asyncio

import pytest

from app import db, orchestrator
from app.agents.base import Agent
from app.agents.verify import check_question
from app.events import EventBus
from app.providers import fixtures

RUN_ID = "test-verify-batch"


def test_pass_when_substitution_matches():
    question = {
        "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的顶点纵坐标。",
        "verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": -1.0},
    }
    passed, detail = check_question(question)
    assert passed is True
    assert "期望 -1" in detail


def test_reject_when_expect_disagrees():
    question = {
        "stem_latex": r"若 $y = x^{2} - 4x + 3$ 在 $x = 2$ 处的函数值为 $m$。",
        "verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": 1.0},
    }
    passed, detail = check_question(question)
    assert passed is False
    assert "期望 1" in detail


def test_reject_when_expression_unparsable():
    passed, detail = check_question({"verify": {"expr": "x**", "expect": 1}})
    assert passed is False
    assert "无法解析" in detail


def test_without_verify_field_is_not_blocking():
    passed, detail = check_question({"stem_latex": "1 + 1 = ?", "answer": "2"})
    assert passed is True
    assert "无可验算表达式" in detail


def test_with_null_verify_is_not_blocking():
    passed, detail = check_question({"stem_latex": "1 + 1 = ?", "verify": None})
    assert passed is True
    assert "无可验算表达式" in detail


def test_tolerance_absorbs_float_noise():
    question = {"verify": {"expr": "1/3", "subs": {}, "expect": 0.3333333333}}
    passed, _ = check_question(question)
    assert passed is True


@pytest.mark.parametrize("verify", ["1+1", [1, 2], 5, 1.5, "", []])
def test_non_object_verify_is_rejected(verify):
    passed, detail = check_question({"verify": verify})

    assert passed is False
    assert detail == "verify 不是对象"


def test_huge_expect_is_rejected_without_overflow():
    """10**400 转 float 抛 OverflowError：这是坏题，不能让整批发题崩掉。"""
    passed, detail = check_question(
        {"verify": {"expr": "1 + 1", "subs": {}, "expect": 10**400}}
    )

    assert passed is False
    assert "期望值不是数值" in detail


class _FixedCompose(Agent):
    """固定出 5 题，其中第 3 题的 verify 是坏数据（字符串）。"""

    name = "compose"

    async def run(self) -> list[dict]:
        questions = [
            {
                "stem_latex": f"第 {index} 题：1 + 1 = ?",
                "qtype": "fill",
                "answer": "2",
                "verify": {"expr": "1 + 1", "subs": {}, "expect": 2.0},
                "card_ids": [],
            }
            for index in range(1, 6)
        ]
        questions[2]["verify"] = "坏掉的 verify"
        self.ctx.state["questions"] = questions
        return questions


def test_one_bad_question_does_not_block_the_other_four(monkeypatch):
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card_ids = db.insert_cards(source_id, fixtures.MOCK_CARDS)
    monkeypatch.setattr(orchestrator, "ComposeAgent", _FixedCompose)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(orchestrator.Orchestrator(bus).generate(RUN_ID, card_ids, "出题", 5))

    run = bus.get(RUN_ID)
    assert run is not None and run.closed is True
    done = [event for event in run.history if event.phase == "done"]
    assert len(done) == 1
    delivered = done[0].payload["result"]["questions"]
    assert len(delivered) == 4
    assert all(question["verified"] for question in delivered)
    assert all(question["verify_log"] for question in delivered)
    assert not any("坏掉的 verify" == str(q.get("verify")) for q in delivered)
