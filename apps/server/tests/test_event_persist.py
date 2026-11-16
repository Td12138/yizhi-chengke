"""事件落库与回放（v0.1.5）：run 回收 / 重启后仍可取回历史；落库故障不影响 SSE。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.events import Event, EventBus
from app.main import app

RUN_ID = "test-persist-run"


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _publish(bus: EventBus, run_id: str, phase: str, message: str = "", **payload):
    asyncio.run(bus.publish(Event(run_id, "orchestrator", phase, message, payload)))


def test_publish_persist_events_with_message_and_payload():
    bus = EventBus()
    bus.create(RUN_ID)
    _publish(bus, RUN_ID, "running", "开始", step=1)
    _publish(bus, RUN_ID, "done", "结束", result={"ok": True})
    bus.close(RUN_ID)

    events = db.list_events(RUN_ID)
    assert len(events) == 2
    assert events[0]["message"] == "开始"
    assert events[0]["payload"] == {"step": 1}
    assert events[1]["phase"] == "done"
    assert events[1]["payload"] == {"result": {"ok": True}}
    assert events[0]["ts"] <= events[1]["ts"]


def test_persist_switch_off_keeps_memory_only(monkeypatch):
    monkeypatch.setattr(config, "EVENT_PERSIST", False)
    bus = EventBus()
    bus.create(RUN_ID)
    _publish(bus, RUN_ID, "done", "结束")
    bus.close(RUN_ID)

    assert db.list_events(RUN_ID) == []
    # 内存历史不受影响
    assert len(bus.get(RUN_ID).history) == 1


def test_persist_failure_does_not_break_sse(monkeypatch):
    def broken_insert(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "insert_event", broken_insert)
    bus = EventBus()
    bus.create(RUN_ID)
    _publish(bus, RUN_ID, "done", "结束")
    bus.close(RUN_ID)

    run = bus.get(RUN_ID)
    assert run is not None and len(run.history) == 1
    lines = asyncio.run(_collect(bus.stream(RUN_ID)))
    assert any('"phase": "done"' in line for line in lines)


async def _collect(stream) -> list[str]:
    return [line async for line in stream]


def test_replay_survives_bus_restart():
    """新 EventBus（模拟重启）内存里没有 run，但落库事件仍可回放。"""
    bus = EventBus()
    bus.create(RUN_ID)
    _publish(bus, RUN_ID, "running", "开始")
    _publish(bus, RUN_ID, "done", "结束", result={"ok": True})
    bus.close(RUN_ID)

    restarted = EventBus()
    assert restarted.get(RUN_ID) is None
    events = db.list_events(RUN_ID)
    assert [e["phase"] for e in events] == ["running", "done"]


def test_prune_keeps_recent_runs(monkeypatch):
    monkeypatch.setattr(config, "EVENT_PERSIST_RUNS", 2)
    bus = EventBus()
    for index in range(4):
        run_id = f"run-{index}"
        bus.create(run_id)
        _publish(bus, run_id, "done", f"结束 {index}")
        bus.close(run_id)

    # 创建 run-3 时触发清理：最旧的 run-0 被回收，其余保留
    assert db.list_events("run-0") == []
    assert len(db.list_events("run-1")) == 1
    assert len(db.list_events("run-2")) == 1
    assert len(db.list_events("run-3")) == 1


def test_api_run_events_endpoint(client):
    bus = EventBus()
    bus.create(RUN_ID)
    _publish(bus, RUN_ID, "done", "结束", result={"ok": True})
    bus.close(RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/events")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["events"][0]["message"] == "结束"
    assert body["events"][0]["payload"] == {"result": {"ok": True}}


def test_api_run_events_unknown_returns_404(client):
    assert client.get("/api/runs/no-such-run/events").status_code == 404


def test_pipeline_events_are_persisted(client):
    """真实编排跑一遍：落库事件与 SSE 历史同构。"""
    from app import orchestrator
    from app.providers import fixtures

    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card_ids = db.insert_cards(source_id, fixtures.MOCK_CARDS)
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(orchestrator.Orchestrator(bus).generate(RUN_ID, card_ids, "出题", 5))

    run = bus.get(RUN_ID)
    persisted = db.list_events(RUN_ID)
    assert len(persisted) == len(run.history)
    assert [(e["agent"], e["phase"]) for e in persisted] == [
        (e.agent, e.phase) for e in run.history
    ]