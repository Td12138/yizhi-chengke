"""修正留痕（v0.1.5）：真实变更写 card_revision，原值提交不产生记录。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.providers import fixtures

MISSING_ID = 99999


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _seed_card() -> dict:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    card_id = db.insert_cards(source_id, [fixtures.MOCK_CARDS[1]])[0]
    return db.get_card(card_id)


def test_patch_records_old_and_new_values():
    card = _seed_card()
    db.update_card(card["id"], {"title": "改后的标题"})

    revisions = db.list_card_revisions(card["id"])
    assert len(revisions) == 1
    assert revisions[0]["field"] == "title"
    assert revisions[0]["old_value"] == card["title"]
    assert revisions[0]["new_value"] == "改后的标题"
    assert revisions[0]["created_at"] > 0
    # 卡片带最近修正时间
    assert db.get_card(card["id"])["revised_at"] is not None


def test_patch_same_value_writes_nothing():
    card = _seed_card()
    db.update_card(card["id"], {"title": card["title"]})
    assert db.list_card_revisions(card["id"]) == []
    assert db.get_card(card["id"])["revised_at"] is None


def test_patch_multiple_fields_records_each():
    card = _seed_card()
    db.update_card(card["id"], {"title": "新标题", "summary": "新摘要"})
    revisions = db.list_card_revisions(card["id"])
    assert {r["field"] for r in revisions} == {"title", "summary"}


def test_revisions_are_newest_first():
    card = _seed_card()
    db.update_card(card["id"], {"title": "第一次"})
    db.update_card(card["id"], {"title": "第二次"})
    revisions = db.list_card_revisions(card["id"])
    assert [r["new_value"] for r in revisions] == ["第二次", "第一次"]
    # 第二条的改前值是第一次的改后值
    assert revisions[0]["old_value"] == "第一次"


def test_whitelist_still_enforced_with_revisions():
    card = _seed_card()
    db.update_card(card["id"], {"subject": "不能改的字段", "title": "能改"})
    revisions = db.list_card_revisions(card["id"])
    assert [r["field"] for r in revisions] == ["title"]
    assert db.get_card(card["id"])["subject"] == card["subject"]


def test_api_revisions_endpoint(client):
    card = _seed_card()
    client.patch(f"/api/cards/{card['id']}", json={"body": "修正后的正文"})

    response = client.get(f"/api/cards/{card['id']}/revisions")
    assert response.status_code == 200
    revisions = response.json()["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["field"] == "body"
    assert revisions[0]["old_value"] == card["body"]
    assert revisions[0]["new_value"] == "修正后的正文"


def test_api_revisions_missing_card_returns_404(client):
    assert client.get(f"/api/cards/{MISSING_ID}/revisions").status_code == 404


def test_api_card_carries_revised_at(client):
    card = _seed_card()
    assert client.get("/api/cards").json()["cards"][0]["revised_at"] is None
    client.patch(f"/api/cards/{card['id']}", json={"title": "留痕"})
    updated = client.get("/api/cards").json()["cards"][0]
    assert isinstance(updated["revised_at"], float)