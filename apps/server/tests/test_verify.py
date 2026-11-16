"""质检三态：验算通过 / 验算驳回 / 没有 verify 字段（结构检查，v0.1.5 三级语义）。"""

from __future__ import annotations

import asyncio

import pytest

from app import config, db, orchestrator
from app.agents.base import Agent
from app.agents.verify import (
    LEVEL_STRUCTURAL,
    LEVEL_SYMBOLIC,
    LEVEL_UNCHECKED,
    check_question,
    check_question_detailed,
    structural_check,
)
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


def test_without_verify_field_passes_structural_check():
    """结构完整的无 verify 题放行，但级别是 structural，不是 symbolic。"""
    passed, detail, level = check_question_detailed(
        {"stem_latex": "1 + 1 = ?", "answer": "2"}
    )
    assert passed is True
    assert "无可验算表达式" in detail
    assert level == LEVEL_STRUCTURAL


def test_null_verify_without_answer_is_rejected_by_default():
    """v0.1.5 默认开结构检查：缺答案的无 verify 题不再静默放行。"""
    passed, detail, level = check_question_detailed(
        {"stem_latex": "1 + 1 = ?", "verify": None}
    )
    assert passed is False
    assert "答案为空" in detail
    assert level == LEVEL_STRUCTURAL


def test_null_verify_legacy_switch_restores_old_behavior(monkeypatch):
    """关掉开关 = 旧行为逐字回退：放行 + 原文案，级别如实记 unchecked。"""
    monkeypatch.setattr(config, "VERIFY_STRUCTURE_CHECK", False)
    passed, detail, level = check_question_detailed(
        {"stem_latex": "1 + 1 = ?", "verify": None}
    )
    assert passed is True
    assert detail == "无可验算表达式，仅做结构检查"
    assert level == LEVEL_UNCHECKED


def test_tolerance_absorbs_float_noise():
    question = {"verify": {"expr": "1/3", "subs": {}, "expect": 0.3333333333}}
    passed, _ = check_question(question)
    assert passed is True


@pytest.mark.parametrize("verify", ["1+1", [1, 2], 5, 1.5])
def test_non_object_verify_is_rejected(verify):
    passed, detail = check_question({"verify": verify})

    assert passed is False
    assert detail == "verify 不是对象"


@pytest.mark.parametrize("verify", ["", [], 0, False, {}])
def test_empty_verify_without_stem_is_rejected_by_default(verify):
    """空 verify 走结构检查：连题干都没有的题默认被拦下。"""
    passed, detail, level = check_question_detailed({"verify": verify})

    assert passed is False
    assert "题干为空或过短" in detail
    assert level == LEVEL_STRUCTURAL


@pytest.mark.parametrize("verify", ["", [], 0, False, {}])
def test_empty_verify_legacy_switch_is_not_blocking(verify, monkeypatch):
    monkeypatch.setattr(config, "VERIFY_STRUCTURE_CHECK", False)
    passed, detail = check_question({"verify": verify})

    assert passed is True
    assert detail == "无可验算表达式，仅做结构检查"


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
    assert all(question["verify_level"] == LEVEL_SYMBOLIC for question in delivered)
    assert not any("坏掉的 verify" == str(q.get("verify")) for q in delivered)


# ---- 结构检查本身（v0.1.5）----


def test_structural_check_accepts_complete_fill():
    passed, detail = structural_check(
        {"stem_latex": "1 + 1 = ?", "qtype": "fill", "answer": "2"}
    )
    assert passed is True
    assert "结构检查通过" in detail


def test_structural_check_accepts_choice_letter_in_range():
    passed, _ = structural_check(
        {
            "stem_latex": "1 + 1 = ?",
            "qtype": "choice",
            "options": ["1", "2", "3"],
            "answer": "B",
        }
    )
    assert passed is True


def test_structural_check_accepts_choice_answer_as_option_text():
    passed, _ = structural_check(
        {
            "stem_latex": "1 + 1 = ?",
            "qtype": "choice",
            "options": ["1", "2", "3"],
            "answer": "2",
        }
    )
    assert passed is True


@pytest.mark.parametrize(
    "question, reason",
    [
        ({"stem_latex": "", "answer": "2"}, "题干为空或过短"),
        ({"stem_latex": "1+1", "answer": "2"}, "题干为空或过短"),
        ({"stem_latex": "1 + 1 = ?", "answer": ""}, "答案为空"),
        ({"stem_latex": "1 + 1 = ?", "answer": "2", "qtype": "proof"}, "未知题型"),
        (
            {"stem_latex": "1 + 1 = ?", "qtype": "choice", "options": ["1"], "answer": "A"},
            "选项不足",
        ),
        (
            {
                "stem_latex": "1 + 1 = ?",
                "qtype": "choice",
                "options": ["1", "2"],
                "answer": "D",
            },
            "不在选项范围",
        ),
    ],
)
def test_structural_check_rejects_bad_shapes(question, reason):
    passed, detail = structural_check(question)
    assert passed is False
    assert reason in detail


def test_symbolic_level_when_verify_present():
    _, _, level = check_question_detailed(
        {"verify": {"expr": "1 + 1", "subs": {}, "expect": 2.0}}
    )
    assert level == LEVEL_SYMBOLIC


class _NoVerifyCompose(Agent):
    """出一道结构完整但没有 verify 的题：应标 structural 交付。"""

    name = "compose"

    async def run(self) -> list[dict]:
        questions = [
            {
                "stem_latex": "1 + 1 = ?",
                "qtype": "fill",
                "answer": "2",
                "card_ids": [],
            }
        ]
        self.ctx.state["questions"] = questions
        return questions


def test_pipeline_delivers_structural_level_and_counts(monkeypatch):
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card_ids = db.insert_cards(source_id, fixtures.MOCK_CARDS)
    monkeypatch.setattr(orchestrator, "ComposeAgent", _NoVerifyCompose)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(orchestrator.Orchestrator(bus).generate(RUN_ID, card_ids, "出题", 1))

    run = bus.get(RUN_ID)
    assert run is not None and run.closed is True
    done = [
        event
        for event in run.history
        if event.phase == "done" and event.agent == "orchestrator"
    ]
    assert len(done) == 1
    result = done[0].payload["result"]
    assert result["levels"] == {LEVEL_STRUCTURAL: 1}
    question = result["questions"][0]
    assert question["verified"] is True
    assert question["verify_level"] == LEVEL_STRUCTURAL
    # 入库后级别仍在
    stored = db.list_questions()
    assert stored[0]["verify_level"] == LEVEL_STRUCTURAL


def test_pipeline_rejects_structurally_broken_question(monkeypatch):
    """无 verify 且缺答案：三轮都过不了结构检查，整批拒绝交付。"""
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card_ids = db.insert_cards(source_id, fixtures.MOCK_CARDS)

    class _BrokenCompose(Agent):
        name = "compose"

        async def run(self) -> list[dict]:
            questions = [{"stem_latex": "1 + 1 = ?", "card_ids": []}]
            self.ctx.state["questions"] = questions
            return questions

    monkeypatch.setattr(orchestrator, "ComposeAgent", _BrokenCompose)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(orchestrator.Orchestrator(bus).generate(RUN_ID, card_ids, "出题", 1))

    run = bus.get(RUN_ID)
    assert run is not None and run.closed is True
    errors = [event for event in run.history if event.phase == "error"]
    assert len(errors) == 1
    assert "全部题目未通过质检" in errors[0].message
    assert db.list_questions() == []
