"""卡片修正闭环：PATCH 改内容、GET 来源看原文块与关联卡片。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app, csp_applies
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


def test_patch_updates_only_given_fields(client):
    card = _seed_card()
    response = client.patch(
        f"/api/cards/{card['id']}",
        json={"title": "顶点式（人工修正）", "summary": "配方后读出顶点"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == card["id"]
    assert updated["title"] == "顶点式（人工修正）"
    assert updated["summary"] == "配方后读出顶点"
    # 未提交的字段保持原值
    assert updated["body"] == card["body"]
    assert updated["formula"] == card["formula"]
    assert updated["tags"] == card["tags"]


def test_patch_is_visible_in_list(client):
    card = _seed_card()
    client.patch(f"/api/cards/{card['id']}", json={"body": "识别错了，这里改成正确描述"})
    cards = client.get("/api/cards").json()["cards"]
    assert [c["body"] for c in cards if c["id"] == card["id"]] == ["识别错了，这里改成正确描述"]


def test_patch_empty_body_keeps_card(client):
    card = _seed_card()
    response = client.patch(f"/api/cards/{card['id']}", json={})
    assert response.status_code == 200
    assert response.json() == card


def test_patch_missing_card_returns_404(client):
    response = client.patch(f"/api/cards/{MISSING_ID}", json={"title": "无"})
    assert response.status_code == 404


def test_source_returns_blocks_and_cards(client):
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)

    response = client.get(f"/api/sources/{source_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == source_id
    # 绝对路径不下发：前端只用 display_name 与 /image 接口
    assert "image_path" not in body
    assert body["display_name"] == "demo.png"
    assert body["blocks"] == fixtures.MOCK_BLOCKS
    assert [c["title"] for c in body["cards"]] == [c["title"] for c in fixtures.MOCK_CARDS]


def test_source_missing_returns_404(client):
    assert client.get(f"/api/sources/{MISSING_ID}").status_code == 404


def test_source_image_serves_original(client, tmp_path):
    image = tmp_path / "board.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    source_id = db.insert_source(str(image), fixtures.MOCK_BLOCKS)

    response = client.get(f"/api/sources/{source_id}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == image.read_bytes()


def test_source_image_missing_file_returns_404(client, tmp_path):
    source_id = db.insert_source(str(tmp_path / "gone.png"), fixtures.MOCK_BLOCKS)
    assert client.get(f"/api/sources/{source_id}/image").status_code == 404


def test_ask_query_length_is_validated(client):
    assert client.post("/api/ask", json={"query": ""}).status_code == 422
    assert client.post("/api/ask", json={"query": "x" * 2001}).status_code == 422
    assert client.post("/api/ask", json={"query": "对称轴是什么"}).status_code == 200


def test_card_patch_length_limits(client):
    card = _seed_card()

    assert (
        client.patch(f"/api/cards/{card['id']}", json={"title": ""}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/cards/{card['id']}", json={"summary": "x" * 501}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/cards/{card['id']}", json={"title": "x" * 121}
        ).status_code
        == 422
    )


def test_generate_rejects_too_many_card_ids(client):
    response = client.post(
        "/api/generate",
        json={"card_ids": list(range(1, 202)), "requirement": "出题", "count": 3},
    )

    assert response.status_code == 422


def test_csp_only_applies_to_served_frontend():
    """CSP 只补在 SERVE_WEB 托管的页面上：API / SSE 不加，开关关闭也不加。"""
    assert csp_applies("/", serve_web=True, enabled=True) is True
    assert csp_applies("/assets/index.js", serve_web=True, enabled=True) is True
    assert csp_applies("/api/health", serve_web=True, enabled=True) is False
    assert csp_applies("/api/events/abc", serve_web=True, enabled=True) is False
    assert csp_applies("/", serve_web=False, enabled=True) is False
    assert csp_applies("/", serve_web=True, enabled=False) is False
