"""卡片计数接线：追问累加 ask_count，交付题目累加 question_count，失败不阻断主流程。"""

from __future__ import annotations

import asyncio
import sqlite3

from app import config, db
from app.agents.base import RunContext
from app.agents.retrieve import RetrieveAgent
from app.events import EventBus
from app.orchestrator import Orchestrator
from app.providers import fixtures

RUN_ID = "test-counting"
QUERY = "为什么这里要配方"
COUNT = 5


def _counting_error() -> Exception:
    """模拟计数写库失败（如库被锁）。"""
    return sqlite3.OperationalError("database is locked")


def _seed_cards() -> list[dict]:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)
    return db.list_cards()


def _ask_events() -> list:
    bus = EventBus()
    bus.create(RUN_ID)
    ctx = RunContext(RUN_ID, bus, {"query": QUERY})
    asyncio.run(RetrieveAgent(ctx).run())
    return bus.get(RUN_ID).history


def test_retrieve_bumps_ask_count_of_hits():
    _seed_cards()
    hits = db.search_cards(QUERY, config.RAG_TOP_K)
    assert hits, "样例数据应当能召回卡片"

    _ask_events()

    for card in hits:
        stored = db.get_card(card["id"])
        assert stored["ask_count"] == 1
        assert stored["last_asked_at"] is not None


def test_retrieve_reports_cumulative_ask_total():
    _seed_cards()
    _ask_events()
    events = _ask_events()

    delta = [
        event
        for event in events
        if event.agent == "retrieve" and event.phase == "delta"
    ]
    assert len(delta) == 1
    assert "累计被问" in delta[0].message
    assert delta[0].payload["ask_total"] == len(delta[0].payload["cards"]) * 2


def test_retrieve_keeps_answering_when_counting_fails(monkeypatch):
    _seed_cards()

    def boom(_card_ids):
        raise _counting_error()

    monkeypatch.setattr(db, "bump_ask", boom)
    events = _ask_events()

    messages = [event.message for event in events]
    assert any("卡片计数失败" in message for message in messages)
    assert [event for event in events if event.phase == "error"] == []
    assert events[-1].phase == "done"
    assert all(db.get_card(card["id"])["ask_count"] == 0 for card in db.list_cards())


def test_generate_bumps_question_count_of_used_cards():
    cards = _seed_cards()
    bus = EventBus()
    bus.create(RUN_ID)
    orchestrator = Orchestrator(bus)
    asyncio.run(
        orchestrator.generate(RUN_ID, [card["id"] for card in cards], "出几道基础题", COUNT)
    )

    assert [event for event in bus.get(RUN_ID).history if event.phase == "error"] == []
    for card in cards:
        stored = db.get_card(card["id"])
        # 一次交付里同一张卡片只累加一次，不按题目数重复计
        assert stored["question_count"] == 1


def test_generate_still_delivers_when_counting_fails(monkeypatch):
    cards = _seed_cards()

    def boom(_card_ids):
        raise _counting_error()

    monkeypatch.setattr(db, "bump_question", boom)
    bus = EventBus()
    bus.create(RUN_ID)
    orchestrator = Orchestrator(bus)
    asyncio.run(
        orchestrator.generate(RUN_ID, [card["id"] for card in cards], "出几道基础题", COUNT)
    )

    history = bus.get(RUN_ID).history
    assert [event for event in history if event.phase == "error"] == []
    done = [event for event in history if event.agent == "orchestrator" and event.phase == "done"]
    assert len(done) == 1
    assert len(done[0].payload["result"]["questions"]) == COUNT
    assert any("卡片计数失败" in event.message for event in history)
