"""判分：归一化要能吃掉 LaTeX 记号、空白与中文标点；choice 只看选项字母。"""

from __future__ import annotations

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
