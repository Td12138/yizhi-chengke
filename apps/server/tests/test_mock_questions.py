"""mock 命题：按卡片主题出对应的 5 道题，且第 4 题仍固定用于演示驳回。"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.verify import check_question
from app.providers import fixtures, llm
from app.providers.fixtures import SETS, mock_questions

# 第 1 轮固定第 4 题失败，第 2 轮全通过——三套题都必须满足
FIRST_ROUND = [True, True, True, False, True]
SECOND_ROUND = [True] * 5


def _verdicts(questions: list[dict]) -> list[bool]:
    return [check_question(question)[0] for question in questions]


@pytest.mark.parametrize("key", ["quadratic", "equation", "trig"])
def test_each_set_has_five_questions_with_same_verdict_pattern(key):
    cards = SETS[key]["cards"]

    assert _verdicts(mock_questions(1, cards)) == FIRST_ROUND
    assert _verdicts(mock_questions(2, cards)) == SECOND_ROUND


@pytest.mark.parametrize("key", ["quadratic", "equation", "trig"])
def test_question_stems_follow_the_card_set(key):
    questions = mock_questions(1, SETS[key]["cards"])
    stems = " ".join(question["stem_latex"] for question in questions)

    assert len(questions) == 5
    if key == "trig":
        assert "sin" in stems or "tan" in stems or "周期" in stems
        assert "对称轴" not in stems
    elif key == "equation":
        assert "方程" in stems or "根" in stems
        assert "对称轴" not in stems
    else:
        assert "对称轴" in stems


def test_unknown_cards_fall_back_to_quadratic():
    assert fixtures.detect_cards_set([]) == "quadratic"
    assert fixtures.detect_cards_set(None) == "quadratic"
    assert fixtures.detect_cards_set([{"tags": ["语文"], "subject": "语文"}]) == "quadratic"

    stems = " ".join(q["stem_latex"] for q in mock_questions(1, []))
    assert "对称轴" in stems


def test_db_style_tag_strings_are_detected():
    # 从 SQLite 取出来的 tags 是逗号串，不是数组
    cards = [{"tags": "三角函数,诱导公式", "subject": "数学"}]
    assert fixtures.detect_cards_set(cards) == "trig"

    cards = [{"tags": "一元二次方程,求根公式", "subject": "数学"}]
    assert fixtures.detect_cards_set(cards) == "equation"


def test_llm_mock_compose_uses_cards():
    trig_cards = SETS["trig"]["cards"]
    result = asyncio.run(
        llm.chat_json(
            "compose",
            {"round": 1, "count": 5, "requirement": "出几道三角函数的题", "cards": trig_cards},
        )
    )

    stems = " ".join(question["stem_latex"] for question in result["questions"])
    assert "sin" in stems or "tan" in stems
    assert "对称轴" not in stems


def test_all_questions_have_sandbox_safe_verify():
    # 三套题的 expr 必须能过安全解析，否则演示时会整批被驳回
    for sample in SETS.values():
        for question in mock_questions(2, sample["cards"]):
            passed, detail = check_question(question)
            assert passed is True, detail
