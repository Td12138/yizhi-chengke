"""混合检索（v0.1.5）：缺省 legacy 不变；hybrid 走 RRF 融合，端点故障回退。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import config, db, embedding, retrieval
from app.events import EventBus
from app.orchestrator import Orchestrator
from app.providers import fixtures

RUN_ID = "test-hybrid"


def _seed_all_cards() -> list[int]:
    ids: list[int] = []
    for blocks, cards in (
        (fixtures.QUADRATIC_BLOCKS, fixtures.QUADRATIC_CARDS),
        (fixtures.EQUATION_BLOCKS, fixtures.EQUATION_CARDS),
        (fixtures.TRIG_BLOCKS, fixtures.TRIG_CARDS),
    ):
        source_id = db.insert_source("demo.png", blocks)
        ids.extend(db.insert_cards(source_id, cards))
    return ids


def _fake_vector(text: str) -> list[float]:
    """确定性伪嵌入：主题词 → 单位向量，模拟语义相近。"""
    lowered = text.lower()
    if any(word in lowered for word in ("vertex", "配方", "顶点", "抛物线", "开口")):
        return [1.0, 0.0, 0.0]
    if any(word in lowered for word in ("root", "方程", "求根")):
        return [0.0, 1.0, 0.0]
    if any(word in lowered for word in ("sin", "三角", "周期")):
        return [0.0, 0.0, 1.0]
    return [0.0, 0.0, 0.0]


def _install_fake_endpoint(monkeypatch, calls: list | None = None, fail: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(json.loads(request.content))
        if fail:
            raise httpx.ConnectError("connection refused")
        body = json.loads(request.content)
        data = [
            {"index": index, "embedding": _fake_vector(text)}
            for index, text in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data})

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(embedding, "_http_client", factory)
    monkeypatch.setattr(config, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(config, "EMBED_BASE_URL", "http://fake-embed/v1")
    monkeypatch.setattr(config, "EMBED_MODEL", "test-model")


# ---- 缺省行为回归 ----


def test_default_mode_is_legacy():
    assert config.RETRIEVAL_MODE == "legacy"


def test_legacy_mode_never_calls_hybrid(monkeypatch):
    _seed_all_cards()

    async def boom(*_args, **_kwargs):
        raise AssertionError("legacy 模式不应调用 hybrid_search")

    monkeypatch.setattr(retrieval, "hybrid_search", boom)
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ask(RUN_ID, "为什么这里要配方？"))
    run = bus.get(RUN_ID)
    done = [e for e in run.history if e.agent == "orchestrator" and e.phase == "done"]
    assert done and done[0].payload["result"]["citations"]


# ---- RRF 纯函数 ----


def test_rrf_fuse_prefers_consensus():
    # A 在两路都排第 2，B 只在一路排第 1：k=60 时 A 胜出
    fused = retrieval.rrf_fuse([[1, 2], [3, 2]], k=60)
    assert fused[0] == 2
    assert set(fused) == {1, 2, 3}


def test_rrf_fuse_single_ranking_keeps_order():
    assert retrieval.rrf_fuse([[5, 4, 3]]) == [5, 4, 3]


# ---- hybrid 端到端（伪端点）----


def test_hybrid_recalls_semantic_query_legacy_misses(monkeypatch):
    """英文同义问句 2-gram 召回不到，向量路能召回（L5 的修复点）。"""
    _seed_all_cards()
    assert db.search_cards("how to find the vertex", 3) == []

    _install_fake_endpoint(monkeypatch)
    cards, note = asyncio.run(retrieval.hybrid_search("how to find the vertex", 3))
    assert cards, "hybrid 应召回二次函数卡片"
    assert all("二次函数" in card["tags"] for card in cards)
    assert "hybrid" in note


def test_hybrid_caches_embeddings(monkeypatch):
    _seed_all_cards()
    calls: list = []
    _install_fake_endpoint(monkeypatch, calls=calls)

    asyncio.run(retrieval.hybrid_search("how to find the vertex", 3))
    first_round = len(calls)
    assert first_round == 2  # 查询 1 次 + 卡片批量 1 次（9 张 < 批上限 16）

    asyncio.run(retrieval.hybrid_search("三角函数的周期怎么算", 3))
    assert len(calls) == first_round + 1  # 卡片向量已缓存，只嵌查询


def test_hybrid_reembeds_on_model_change(monkeypatch):
    _seed_all_cards()
    calls: list = []
    _install_fake_endpoint(monkeypatch, calls=calls)
    asyncio.run(retrieval.hybrid_search("how to find the vertex", 3))
    before = len(calls)

    monkeypatch.setattr(config, "EMBED_MODEL", "another-model")
    asyncio.run(retrieval.hybrid_search("how to find the vertex", 3))
    assert len(calls) == before + 2  # 查询 + 全量卡片重嵌


def test_hybrid_falls_back_when_endpoint_down(monkeypatch):
    _seed_all_cards()
    _install_fake_endpoint(monkeypatch, fail=True)

    legacy = db.search_cards("为什么这里要配方？", 3)
    cards, note = asyncio.run(retrieval.hybrid_search("为什么这里要配方？", 3))
    assert [c["id"] for c in cards] == [c["id"] for c in legacy]
    assert "回退" in note


def test_hybrid_without_endpoint_config_falls_back(monkeypatch):
    _seed_all_cards()
    monkeypatch.setattr(config, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(config, "EMBED_BASE_URL", "")
    cards, note = asyncio.run(retrieval.hybrid_search("为什么这里要配方？", 3))
    assert cards == db.search_cards("为什么这里要配方？", 3)
    assert "未配置" in note


def test_retrieve_agent_reports_hybrid_path(monkeypatch):
    _seed_all_cards()
    _install_fake_endpoint(monkeypatch)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ask(RUN_ID, "how to find the vertex"))
    run = bus.get(RUN_ID)
    notes = [
        e.message
        for e in run.history
        if e.agent == "retrieve" and e.message.startswith("检索路径")
    ]
    assert notes and "hybrid" in notes[0]
    done = [e for e in run.history if e.agent == "orchestrator" and e.phase == "done"]
    assert done[0].payload["result"]["citations"], "出处必须标注"


# ---- 嵌入客户端协议守卫 ----


def test_embed_texts_rejects_count_mismatch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    monkeypatch.setattr(
        embedding,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(config, "EMBED_BASE_URL", "http://fake/v1")
    monkeypatch.setattr(config, "EMBED_MODEL", "m")
    with pytest.raises(embedding.EmbeddingError):
        asyncio.run(embedding.embed_texts(["a", "b"]))


def test_embed_texts_rejects_non_finite(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [float("nan")]}]}
        )

    monkeypatch.setattr(
        embedding,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(config, "EMBED_BASE_URL", "http://fake/v1")
    monkeypatch.setattr(config, "EMBED_MODEL", "m")
    with pytest.raises(embedding.EmbeddingError):
        asyncio.run(embedding.embed_texts(["a"]))


def test_cosine_zero_vector_is_zero():
    assert embedding.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert embedding.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert embedding.cosine([1.0], [1.0, 0.0]) == 0.0