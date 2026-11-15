"""编排回环：mock 下第 1 轮必被驳回 1 题，第 2 轮全通过后才交付。"""

from __future__ import annotations

import asyncio

from app import db
from app.events import EventBus
from app.orchestrator import Orchestrator
from app.providers import fixtures

RUN_ID = "test-generate"
COUNT = 5


def _seed_card_ids() -> list[int]:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)
    return [card["id"] for card in db.list_cards()]


def _run_generate() -> list:
    bus = EventBus()
    bus.create(RUN_ID)
    orchestrator = Orchestrator(bus)
    asyncio.run(orchestrator.generate(RUN_ID, _seed_card_ids(), "出几道基础题", COUNT))
    run = bus.get(RUN_ID)
    assert run is not None
    assert run.closed is True
    return run.history


def test_first_round_rejects_exactly_one_question():
    events = _run_generate()
    verdicts = [e.payload.get("ok") for e in events if e.agent == "verify" and e.phase == "delta"]
    assert len(verdicts) == COUNT * 2, "应当跑两轮质检，每轮 5 题"
    assert verdicts[:COUNT] == [True, True, True, False, True]
    assert verdicts[COUNT:] == [True] * COUNT


def test_rejection_sends_questions_back_to_compose():
    events = _run_generate()
    compose_queued = [e for e in events if e.agent == "compose" and e.phase == "queued"]
    assert len(compose_queued) == 2, "被驳回后命题 Agent 必须重跑一轮"

    rejected = [e for e in events if e.agent == "verify" and e.phase == "rejected"]
    assert len(rejected) == 1
    assert "打回" in rejected[0].message

    bounced = [e for e in events if e.agent == "orchestrator" and e.phase == "delta"]
    assert any("打回命题 Agent" in e.message for e in bounced)


def test_second_round_delivers_all_verified_questions():
    events = _run_generate()
    done = [e for e in events if e.agent == "orchestrator" and e.phase == "done"]
    assert len(done) == 1
    result = done[0].payload["result"]
    assert len(result["questions"]) == COUNT
    assert result["dropped"] == 0
    assert all(q["verified"] for q in result["questions"])

    stored = db.list_questions(limit=COUNT + 1)
    assert len(stored) == COUNT
    assert all(row["verified"] == 1 for row in stored)


def test_run_has_no_error_event():
    events = _run_generate()
    assert [e for e in events if e.phase == "error"] == []
