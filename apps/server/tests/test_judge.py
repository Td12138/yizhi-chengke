"""判分：归一化要能吃掉 LaTeX 记号、空白与中文标点；choice 只看选项字母。

v0.1.5 追加 sympy 等价判定：代数等价式、反写方程、分数小数互化。
"""

from __future__ import annotations

import pytest

from app import config
from app.judge import latex_to_plain, sympy_equivalent
from app.orchestrator import judge, normalize


def test_normalize_strips_latex_noise():
    assert normalize(" $x = 2$ ") == "x=2"
    assert normalize(r"\dfrac{x}{2}") == "dfracx2"
    assert normalize(r"\dfrac{-4}{2}") == "dfrac-42"


def test_normalize_is_case_insensitive():
    assert normalize("X") == normalize("x")


def test_normalize_maps_fullwidth_punctuation():
    assert normalize("（2，-1）") == normalize("(2,-1)")
    # 全角逗号不能直接删掉，否则 (1,2) 会退化成 (12)
    assert normalize("1，2") == "1,2"


def test_normalize_handles_fullwidth_letters_and_minus_sign():
    # NFKC：全角字母、全角数字、U+2212 减号都归一到 ASCII
    assert normalize("ＡＢＣ１２３") == "abc123"
    assert normalize("−1") == "-1"
    assert normalize("Ｘ＝２") == "x=2"


def test_fill_accepts_fullwidth_letters_and_unicode_minus():
    assert judge({"qtype": "fill", "answer": "$-1$"}, "−1") is True
    assert judge({"qtype": "fill", "answer": "x"}, "Ｘ") is True


def test_fill_exact_match():
    question = {"qtype": "fill", "answer": "4"}
    assert judge(question, "4") is True


def test_fill_accepts_bare_value_for_equation_answer():
    # 标准答案是「x = 2」时，只填等号右边的 2 也算对
    question = {"qtype": "fill", "answer": r"$x = 2$"}
    assert judge(question, "2") is True


def test_fill_equivalent_forms_are_all_accepted():
    cases = [
        ("$x = 2$", "x=2"),
        ("$-1$", "-1"),
        ("$(2,\\ -1)$", "(2,-1)"),
        ("$(2,\\ -1)$", "2, -1"),
        ("$-1$", "－1"),  # 全角负号
        ("$x = 2$", "２"),  # 全角数字
        ("$2$", "2.0"),
    ]
    for answer, given in cases:
        assert judge({"qtype": "fill", "answer": answer}, given) is True, given


def test_fill_still_rejects_wrong_answers():
    question = {"qtype": "fill", "answer": r"$x = 2$"}
    assert judge(question, "3") is False
    assert judge(question, "x=3") is False
    assert judge(question, "-2") is False
    assert judge({"qtype": "fill", "answer": r"$(2,\ -1)$"}, "(-2, 1)") is False


def test_fill_ignores_latex_wrapper_and_spaces():
    question = {"qtype": "fill", "answer": r"$x = 2$"}
    assert judge(question, "x=2") is True
    assert judge(question, "  x = 2  ") is True


def test_fill_polynomial_written_differently():
    question = {"qtype": "fill", "answer": r"$y = (x-2)^{2} - 1$"}
    assert judge(question, r"y=(x-2)^2-1") is True


def test_superscript_digits_equal_caret_form():
    """x² 与 x^2、10⁻¹ 与 10^-1 是同一个答案，NFKC 前要先展开上标。"""
    assert normalize("x²") == "x^2"
    assert judge({"qtype": "fill", "answer": r"$x^{2}$"}, "x²") is True
    assert judge({"qtype": "fill", "answer": "10^-1"}, "10⁻¹") is True
    assert judge({"qtype": "fill", "answer": r"$x^{2}$"}, "x³") is False


def test_fill_wrong_answer():
    question = {"qtype": "fill", "answer": r"$x = 2$"}
    assert judge(question, "x=3") is False
    assert judge(question, "") is False


def test_choice_matches_first_letter():
    question = {"qtype": "choice", "options": ["0 个", "1 个", "2 个"], "answer": "C"}
    assert judge(question, "C") is True
    assert judge(question, "c") is True
    assert judge(question, "C 选项") is True
    assert judge(question, "B") is False


def test_choice_rejects_empty_or_missing_answer():
    assert judge({"qtype": "choice", "answer": "C"}, "") is False
    assert judge({"qtype": "choice", "answer": ""}, "C") is False


def test_fill_by_default_when_qtype_missing():
    question = {"answer": "2"}
    assert judge(question, "2") is True


# ---- sympy 等价判定（v0.1.5）----


@pytest.mark.parametrize(
    "answer, given",
    [
        (r"$x = 2$", "2 = x"),  # 反写方程
        (r"$(x-2)(x-3)$", "x^2 - 5x + 6"),  # 因式分解 ↔ 展开
        (r"$x^{2} - 5x + 6$", "(x-2)(x-3)"),
        (r"$\dfrac{1}{2}$", "0.5"),  # 分数 ↔ 小数
        (r"$\frac{4}{2}$", "2"),
        (r"$x + 1$", "1 + x"),
        (r"$2x$", "x + x"),
        (r"$\sqrt{4}$", "2"),
        (r"$y = (x-2)^{2} - 1$", "y = x^2 - 4x + 3"),
        (r"$x = 2$", r"\frac{4}{2}"),  # 方程 ↔ 纯数（代入验证）
        (r"$\sin(\pi/6)$", "0.5"),
    ],
)
def test_sympy_equivalent_forms(answer, given):
    assert judge({"qtype": "fill", "answer": answer}, given) is True


@pytest.mark.parametrize(
    "answer, given",
    [
        (r"$x = 2$", "x = 3"),
        (r"$x = 2$", "x - 2"),  # 表达式不是方程的解
        (r"$x + 1$", "x + 2"),
        (r"$(x-2)(x-3)$", "x^2 - 5x + 5"),
        (r"$x = 2$", "y = 2"),  # 变量不同
        (r"$\alpha$", "a"),  # 未知 LaTeX 命令不删、不误判
    ],
)
def test_sympy_still_rejects_wrong_answers(answer, given):
    assert judge({"qtype": "fill", "answer": answer}, given) is False


def test_sympy_path_never_crashes_on_garbage():
    for garbage in ("__import__('os')", "x**", "((2**1000)**1000)**1000", "💥", ""):
        assert judge({"qtype": "fill", "answer": "2"}, garbage) is False
        assert sympy_equivalent("2", garbage) is False


def test_judge_sympy_switch_off_restores_legacy(monkeypatch):
    monkeypatch.setattr(config, "JUDGE_SYMPY_EQUIV", False)
    # 旧规则判不了的等价式，关开关后必须仍判错（逐字节回退）
    assert judge({"qtype": "fill", "answer": r"$(x-2)(x-3)$"}, "x^2-5x+6") is False
    assert judge({"qtype": "fill", "answer": r"$x = 2$"}, "2=x") is False
    # 旧规则本来就判对的保持不变
    assert judge({"qtype": "fill", "answer": r"$x = 2$"}, "2") is True


def test_latex_to_plain_keeps_unknown_commands():
    assert latex_to_plain(r"$\alpha + 1$") == r"\alpha + 1"
    assert latex_to_plain(r"\dfrac{-4}{2}") == "((-4)/(2))"
    assert latex_to_plain(r"$x^{2}$") == "x^(2)"
    assert latex_to_plain(r"\left( x \right)") == "( x )"


def test_choice_unaffected_by_sympy():
    question = {"qtype": "choice", "options": ["1", "2"], "answer": "B"}
    assert judge(question, "A") is False
    assert judge(question, "B") is True


# ---- 审查 P1-1 回归：方程右端值不是解 ----


@pytest.mark.parametrize(
    "answer, given",
    [
        ("x/2=2", "2"),  # 解是 4，右端 2 不能判对
        ("x^2=4", "4"),  # 解是 ±2
        ("2x=4", "4"),  # 解是 2
        ("x+1=5", "5"),  # 解是 4
    ],
)
def test_equation_rhs_literal_is_not_the_solution(answer, given):
    assert judge({"qtype": "fill", "answer": answer}, given) is False


@pytest.mark.parametrize(
    "answer, given",
    [
        ("x/2=2", "4"),
        ("x^2=4", "2"),
        ("x^2=4", "-2"),
        ("x=2", "2"),  # 演示路径回归：代入验证通过
        ("x=2", "2.0"),
    ],
)
def test_equation_actual_solution_accepted(answer, given):
    assert judge({"qtype": "fill", "answer": answer}, given) is True


def test_expression_equal_to_one_side_of_identity_accepted():
    # 学生写了恒等式的一侧（去掉 y=）仍算对
    assert judge(
        {"qtype": "fill", "answer": r"$y = (x-2)^{2} - 1$"}, "(x-2)^2-1"
    ) is True
    # 但 x=2 与 x-2 既不是解也不是端，判错
    assert judge({"qtype": "fill", "answer": "x=2"}, "x-2") is False


def test_big_power_skips_sympy_quickly():
    # (x+1)^1000 的等价化简会秒级阻塞：判分路径指数上限直接退回（审查 P2-14）
    import time

    started = time.monotonic()
    assert judge({"qtype": "fill", "answer": "(x+1)^1000"}, "0") is False
    assert time.monotonic() - started < 1.0
