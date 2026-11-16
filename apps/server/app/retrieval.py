"""混合检索（v0.1.5）：2-gram 字段加权 + 向量余弦，RRF 融合排名。

缺省 RETRIEVAL_MODE=legacy，行为与旧版逐字节一致（db.search_cards）。
hybrid 需要嵌入端点；端点故障 / 未配置时回退 legacy 并说明原因，
追问主流程绝不因检索增强失败而中断。

卡片向量懒计算：首次检索时批量嵌入并落 card_embedding 表，
之后命中缓存；换模型（model 字段不同）自动重嵌。
语料规模是个人知识库（几十到几百张卡片），余弦用纯 Python 逐条算即可，
不引 sqlite-vec 等原生扩展，保持零额外依赖。
"""

from __future__ import annotations

from . import config, db, embedding

# 向量路相似度下限：低于此值视为无关，不进 RRF——保住 legacy 的
# 「没有可信命中就返回空、不硬凑出处」设计（db.py:MIN_SCORE 的向量侧对应物）。
# 取值依据：BGE-M3 归一化向量下，同主题问句实测 ≥0.5，无关问句 ≤0.3。
VECTOR_MIN_COSINE = 0.35


def card_text(card: dict) -> str:
    """送嵌入的文本：标题 + 摘要 + 正文，截断到上限。"""
    text = "\n".join(
        str(card.get(field) or "") for field in ("title", "summary", "body")
    )
    return text[: config.EMBED_MAX_CHARS]


def rrf_fuse(rankings: list[list[int]], k: int | None = None) -> list[int]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，rank 从 1 起。

    只出现一次的条目也参与融合；同分按首次出现顺序稳定排序。
    """
    k = config.RRF_K if k is None else k
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, card_id in enumerate(ranking, start=1):
            scores[card_id] = scores.get(card_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda card_id: scores[card_id], reverse=True)


async def _vector_ranking(
    query: str, cards: list[dict]
) -> tuple[list[int], str]:
    """向量路排名：返回 (card_id 按相似度降序, 说明)。失败抛 EmbeddingError。"""
    query_vectors = await embedding.embed_texts([query])
    query_vector = query_vectors[0]

    cached = db.get_card_embeddings([card["id"] for card in cards])
    missing: list[dict] = []
    for card in cards:
        entry = cached.get(card["id"])
        if entry is None or entry["model"] != config.EMBED_MODEL:
            missing.append(card)
    if missing:
        vectors = await embedding.embed_texts([card_text(card) for card in missing])
        db.save_card_embeddings(
            config.EMBED_MODEL,
            {card["id"]: vector for card, vector in zip(missing, vectors)},
        )
        for card, vector in zip(missing, vectors):
            cached[card["id"]] = {"model": config.EMBED_MODEL, "vector": vector}

    scored: list[tuple[float, int]] = []
    for card in cards:
        entry = cached.get(card["id"])
        if entry is None:
            continue
        score = embedding.cosine(query_vector, entry["vector"])
        if score >= VECTOR_MIN_COSINE:
            scored.append((score, card["id"]))
    scored.sort(key=lambda item: item[0], reverse=True)
    note = f"向量路 {len(scored)} 张过阈值（新嵌入 {len(missing)} 张）"
    return [card_id for _, card_id in scored], note


async def hybrid_search(query: str, top_k: int) -> tuple[list[dict], str]:
    """混合检索入口：返回 (卡片列表, 路径说明)。任何故障回退 legacy。"""
    legacy = db.search_cards(query, top_k)
    if not embedding.configured():
        return legacy, "legacy（2-gram）：嵌入端点未配置"

    cards = db.list_cards(limit=config.HYBRID_CORPUS_LIMIT)
    if not cards:
        return legacy, "legacy（2-gram）：卡片库为空"
    try:
        vector_ids, vector_note = await _vector_ranking(query, cards)
    except Exception as exc:
        # 嵌入端点、向量缓存读写任何故障都回退 legacy，追问主流程不中断
        return legacy, f"legacy（2-gram）：向量路不可用已回退（{type(exc).__name__}: {exc}）"

    legacy_ids = [card["id"] for card in legacy]
    fused = rrf_fuse([legacy_ids, vector_ids])
    by_id = {card["id"]: card for card in cards}
    # 先过滤再截断：语料上限外的 legacy 命中不能凭空消失
    result = [by_id[card_id] for card_id in fused if card_id in by_id][:top_k]
    note = f"hybrid（2-gram + 向量 RRF）：{vector_note}"
    return result, note
