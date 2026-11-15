"""统计接口：/api/graph 与 /api/stats 的返回结构与排序（独立 app 挂载路由）。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.kb_routes import router as kb_router

TOTAL_CARDS = 8


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(kb_router)
    return TestClient(app)


def _card(title: str, tags: list[str]) -> dict:
    return {
        "title": title,
        "summary": f"{title} 摘要",
        "subject": "数学",
        "tags": tags,
        "formula": "",
        "body": f"{title} 正文",
    }


def _seed(cards: list[dict]) -> list[int]:
    ids: list[int] = []
    for card in cards:
        source_id = db.insert_source("demo.png", [])
        ids.extend(db.insert_cards(source_id, [card]))
    return ids


def test_graph_returns_nodes_edges_and_tags():
    cards = [_card(f"共享{i}", ["共现", f"独有{i}"]) for i in range(3)]
    cards += [_card(f"填充{i}", [f"填充标签{i}"]) for i in range(TOTAL_CARDS - 3)]
    _seed(cards)

    response = _client().get("/api/graph")
    assert response.status_code == 200
    graph = response.json()

    assert set(graph) == {"nodes", "edges", "tags"}
    assert len(graph["nodes"]) == TOTAL_CARDS
    node = graph["nodes"][0]
    assert set(node) == {
        "id",
        "title",
        "summary",
        "subject",
        "tags",
        "ask_count",
        "question_count",
    }
    assert isinstance(node["tags"], list)
    assert node["ask_count"] == 0 and node["question_count"] == 0

    assert len(graph["edges"]) == 3
    edge = graph["edges"][0]
    assert set(edge) == {"source", "target", "weight", "tags"}
    assert isinstance(edge["source"], int) and isinstance(edge["target"], int)
    assert edge["source"] != edge["target"]
    assert "共现" in graph["tags"]


def test_graph_reflects_counts():
    ids = _seed([_card("被问", ["配方"]), _card("没被问", ["配方"])])
    db.bump_ask([ids[0]])
    db.bump_ask([ids[0]])
    db.bump_question([ids[0]])

    graph = _client().get("/api/graph").json()
    node = next(n for n in graph["nodes"] if n["id"] == ids[0])
    assert node["ask_count"] == 2
    assert node["question_count"] == 1


def test_graph_empty_library():
    graph = _client().get("/api/graph").json()
    assert graph == {"nodes": [], "edges": [], "tags": []}


def test_stats_sorted_by_ask_count_desc():
    cards = [_card(f"卡片{i}", [f"标签{i}"]) for i in range(3)]
    ids = _seed(cards)
    for repeat, card_id in zip((3, 2, 1), ids):
        for _ in range(repeat):
            db.bump_ask([card_id])

    response = _client().get("/api/stats")
    assert response.status_code == 200
    body = response.json()

    top = body["top_asked"]
    # ids 升序、被问次数对应 (3, 2, 1) → 结果按次数降序即 ids 原序
    assert [row["id"] for row in top] == ids
    assert [row["ask_count"] for row in top] == [3, 2, 1]
    assert set(top[0]) == {"id", "title", "ask_count", "question_count"}


def test_stats_limited_to_ten_rows():
    cards = [_card(f"卡片{i}", [f"标签{i}"]) for i in range(12)]
    ids = _seed(cards)
    for repeat, card_id in enumerate(ids):
        for _ in range(repeat):
            db.bump_ask([card_id])

    top = _client().get("/api/stats").json()["top_asked"]
    assert len(top) == db.STATS_TOP_N == 10
    assert [row["id"] for row in top] == list(reversed(ids))[:10]
    assert [row["ask_count"] for row in top] == sorted(
        (row["ask_count"] for row in top), reverse=True
    )
