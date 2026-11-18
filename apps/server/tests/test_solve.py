"""拍题讲题独立入口（v0.1.7）：卡片 → 讲解脚本；缺卡 / 开关关闭都返回 404。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.providers import fixtures

MISSING_ID = 99999


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _seed_card(**overrides) -> dict:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card = dict(fixtures.MOCK_CARDS[1], **overrides)
    card_id = db.insert_cards(source_id, [card])[0]
    return db.get_card(card_id)


def test_solve_returns_title_script_and_params(client):
    """正常路径：返回三字段，讲解文案非空，params 可直接喂给前端画图。"""
    card = _seed_card()
    response = client.post("/api/solve", json={"card_id": card["id"]})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"title", "script", "params"}
    assert data["title"]
    assert data["script"]
    assert set(data["params"]) == {"a", "b", "c", "x_min", "x_max"}
    assert data["params"]["x_max"] > data["params"]["x_min"]


def test_solve_does_not_touch_attempts(client):
    """独立入口只读卡片：不写 attempt、不影响既有判分 / 批改数据。"""
    card = _seed_card()
    client.post("/api/solve", json={"card_id": card["id"]})
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
    assert count == 0


def test_solve_missing_card_returns_404(client):
    response = client.post("/api/solve", json={"card_id": MISSING_ID})
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


def test_solve_disabled_returns_404(client, monkeypatch):
    """开关关闭：接口禁用（404），前端 health 同步报 false 隐藏入口。"""
    card = _seed_card()
    monkeypatch.setattr(config, "SOLVE_ENTRY", False)
    response = client.post("/api/solve", json={"card_id": card["id"]})
    assert response.status_code == 404
    assert "未启用" in response.json()["detail"]
    assert client.get("/api/health").json()["solve_entry"] is False


def test_solve_entry_on_by_default(client):
    """缺省开启：health 报 true（旧配置不写 SOLVE_ENTRY 也能用）。"""
    assert client.get("/api/health").json()["solve_entry"] is True
