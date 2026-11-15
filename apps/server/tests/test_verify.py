"""质检三态：验算通过 / 验算驳回 / 没有 verify 字段（只做结构检查）。"""

from __future__ import annotations

from app.agents.verify import check_question


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
