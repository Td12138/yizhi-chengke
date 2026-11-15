"""知识谱系：泛化标签不建边、权重=共享标签数、无边时按来源兜底串链。"""

from __future__ import annotations

from app import db

# 泛化阈值 0.4：8 张卡片里某标签最多 3 张（37.5%）才参与建边
TOTAL_CARDS = 8


def _card(title: str, tags: list[str]) -> dict:
    return {
        "title": title,
        "summary": f"{title} 摘要",
        "subject": "数学",
        "tags": tags,
        "formula": "",
        "body": f"{title} 正文",
    }


def _seed(cards: list[dict], source_count: int = 1) -> list[int]:
    """先建 source_count 个来源，再把卡片轮转挂上去，返回卡片 id。"""
    source_ids = [db.insert_source(f"src{i}.png", []) for i in range(source_count)]
    ids: list[int] = []
    for index, card in enumerate(cards):
        ids.extend(db.insert_cards(source_ids[index % source_count], [card]))
    return ids


def _filler(count: int, prefix: str = "填充") -> list[dict]:
    return [_card(f"{prefix}{i}", [f"{prefix}标签{i}"]) for i in range(count)]


def _edge_pairs(graph: dict) -> set[tuple[int, int]]:
    return {(e["source"], e["target"]) for e in graph["edges"]}


def test_shared_tag_creates_edges_for_every_pair():
    """3 张卡片共享同一非泛化标签 → C(3,2)=3 条边，权重均为 1。"""
    shared = ["共现"]
    cards = [_card(f"共享{i}", shared + [f"独有{i}"]) for i in range(3)]
    cards += _filler(TOTAL_CARDS - 3)
    ids = _seed(cards)

    graph = db.graph_data()
    assert len(graph["nodes"]) == TOTAL_CARDS
    assert len(graph["edges"]) == 3

    shared_ids = ids[:3]
    expected = {
        tuple(sorted((shared_ids[i], shared_ids[j])))
        for i in range(3)
        for j in range(i + 1, 3)
    }
    assert _edge_pairs(graph) == expected
    assert all(edge["weight"] == 1 for edge in graph["edges"])
    assert all(edge["tags"] == shared for edge in graph["edges"])


def test_edge_weight_counts_shared_tags():
    """两张卡片共享两个标签 → 一条边、权重 2、标签只含参与建边的两个。"""
    cards = [_card("甲", ["配方", "顶点式"]), _card("乙", ["配方", "顶点式"])]
    cards += _filler(TOTAL_CARDS - 2)
    ids = _seed(cards)

    graph = db.graph_data()
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert (edge["source"], edge["target"]) == tuple(sorted(ids[:2]))
    assert edge["weight"] == 2
    assert set(edge["tags"]) == {"配方", "顶点式"}


def test_generic_tag_is_filtered_from_edges():
    """10 张卡片里 6 张共享「学科」→ 该标签占比 60%，不建边；保留在 tags 里。"""
    cards = [_card(f"学科{i}", ["学科"]) for i in range(6)]
    cards += [_card(f"小组{i}", ["学科", "小组"]) for i in range(4)]
    ids = _seed(cards)

    graph = db.graph_data()
    assert len(graph["nodes"]) == 10
    assert "学科" in graph["tags"]
    assert all("学科" in node["tags"] for node in graph["nodes"])
    # 学科被过滤后，只剩「小组」（4/10=40%，未超过阈值）连接那 4 张卡片
    assert all("学科" not in edge["tags"] for edge in graph["edges"])
    group_ids = set(ids[6:])
    assert _edge_pairs(graph) == {
        tuple(sorted((a, b))) for a in group_ids for b in group_ids if a < b
    }
    assert len(graph["edges"]) == 6


def test_fallback_chains_cards_of_same_source():
    """只有泛化标签时一条边都没有 → 同一来源的卡片按 id 串成 N-1 条链。"""
    cards = [_card(f"学科{i}", ["学科"]) for i in range(5)]
    ids = _seed(cards, source_count=1)

    graph = db.graph_data()
    assert len(graph["edges"]) == len(ids) - 1
    assert _edge_pairs(graph) == set(zip(ids, ids[1:]))
    assert all(edge["tags"] == [] and edge["weight"] == 1 for edge in graph["edges"])


def test_cards_without_tags_on_distinct_sources_have_no_edges():
    """无标签且各自独立来源 → 无边（兜底链只在同一来源内成立）。"""
    ids = _seed([_card(f"裸卡片{i}", []) for i in range(4)], source_count=4)
    assert len(set(ids)) == 4

    graph = db.graph_data()
    assert len(graph["nodes"]) == 4
    assert graph["edges"] == []
    assert graph["tags"] == []


def test_duplicate_tags_on_one_card_do_not_create_self_loop():
    cards = [_card("重复标签", ["配方", "配方"]), _card("另一张", ["配方"])]
    cards += _filler(TOTAL_CARDS - 2)
    _seed(cards)

    graph = db.graph_data()
    assert len(graph["nodes"]) == TOTAL_CARDS
    assert all(edge["source"] != edge["target"] for edge in graph["edges"])
