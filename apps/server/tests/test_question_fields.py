"""题目出库字段类型：list 与单题接口必须同构（数组，不是 JSON 文本）。"""

from __future__ import annotations

from app import db

MISSING_ID = 99999

CHOICE_QUESTION = {
    "card_ids": [1, 2],
    "stem_latex": r"$y = x^{2} - 4x + 3$ 与 $x$ 轴的交点个数是？",
    "qtype": "choice",
    "options": ["0 个", "1 个", "2 个"],
    "answer": "C",
    "solution": r"$\Delta = 4 > 0$。",
    "difficulty": "easy",
    "verified": True,
}


def test_get_question_decodes_json_columns():
    question_id = db.insert_questions([CHOICE_QUESTION])[0]
    question = db.get_question(question_id)

    assert question["options"] == CHOICE_QUESTION["options"]
    assert question["card_ids"] == CHOICE_QUESTION["card_ids"]


def test_get_question_matches_list_question_shape():
    question_id = db.insert_questions([CHOICE_QUESTION])[0]

    assert db.get_question(question_id) == db.list_questions(limit=1)[0]


def test_get_question_missing_returns_none():
    assert db.get_question(MISSING_ID) is None


def test_get_question_tolerates_empty_json_columns():
    question_id = db.insert_questions(
        [{"stem_latex": "1 + 1 = ?", "options": [], "card_ids": [], "answer": "2"}]
    )[0]

    question = db.get_question(question_id)
    assert question["options"] == []
    assert question["card_ids"] == []
