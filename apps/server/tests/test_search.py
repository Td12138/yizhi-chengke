"""中文检索：无空格、带口语疑问词也要召回正确卡片。"""

from __future__ import annotations

from app import db
from app.providers import fixtures


def _seed() -> list[int]:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    return db.insert_cards(source_id, fixtures.MOCK_CARDS)


def test_why_formula_recalls_vertex_card():
    _seed()
    hits = db.search_cards("为什么这里要配方", 3)
    assert hits, "口语化中文查询没有召回任何卡片"
    assert hits[0]["title"] == "顶点式与对称轴"
    assert "顶点式与对称轴" in [card["title"] for card in hits]


def test_natural_question_recalls_matching_card():
    _seed()
    hits = db.search_cards("判别式决定几个零点", 3)
    assert hits[0]["title"] == "判别式与零点个数"


def test_unrelated_query_returns_empty():
    _seed()
    assert db.search_cards("今天天气怎么样", 3) == []


def test_empty_query_returns_empty():
    _seed()
    assert db.search_cards("   ", 3) == []
