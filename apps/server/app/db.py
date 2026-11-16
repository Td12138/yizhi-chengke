"""SQLite 存储层：每表职责单一，写入函数只做一件事。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  image_path TEXT    NOT NULL,
  raw_json   TEXT    NOT NULL,
  created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS card (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id  INTEGER NOT NULL,
  title      TEXT    NOT NULL,
  summary    TEXT    NOT NULL DEFAULT '',
  subject    TEXT    NOT NULL DEFAULT '',
  tags       TEXT    NOT NULL DEFAULT '',
  formula    TEXT    NOT NULL DEFAULT '',
  body       TEXT    NOT NULL DEFAULT '',
  created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS question (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  card_ids   TEXT    NOT NULL DEFAULT '[]',
  stem_latex TEXT    NOT NULL,
  qtype      TEXT    NOT NULL DEFAULT 'fill',
  options    TEXT    NOT NULL DEFAULT '[]',
  answer     TEXT    NOT NULL DEFAULT '',
  solution   TEXT    NOT NULL DEFAULT '',
  difficulty TEXT    NOT NULL DEFAULT 'medium',
  verified   INTEGER NOT NULL DEFAULT 0,
  verify_log TEXT    NOT NULL DEFAULT '',
  created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS attempt (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL,
  user_answer TEXT    NOT NULL,
  is_correct  INTEGER NOT NULL,
  error_reason TEXT   NOT NULL DEFAULT '',
  created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_event (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id  TEXT    NOT NULL,
  agent   TEXT    NOT NULL,
  phase   TEXT    NOT NULL,
  payload TEXT    NOT NULL DEFAULT '{}',
  ts      REAL    NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
    _ensure_columns()


# 后加列：老库用 ALTER TABLE 补齐，避免重建丢数据
CARD_EXTRA_COLUMNS = {
    "ask_count": "INTEGER NOT NULL DEFAULT 0",
    "question_count": "INTEGER NOT NULL DEFAULT 0",
    "last_asked_at": "REAL",
}

# 原图归档后才有值的列：display_name=归档文件名，renamed_at=改名时间（没改名为 NULL）
SOURCE_EXTRA_COLUMNS = {
    "display_name": "TEXT",
    "renamed_at": "REAL",
}

# v0.1.5 验算级别：symbolic / structural / unchecked；老数据为空串，UI 按 verified 回退显示
QUESTION_EXTRA_COLUMNS = {
    "verify_level": "TEXT NOT NULL DEFAULT ''",
}

# 表名 → 后加列；加新列只改这张表，迁移逻辑不用动
EXTRA_COLUMNS = {
    "card": CARD_EXTRA_COLUMNS,
    "source": SOURCE_EXTRA_COLUMNS,
    "question": QUESTION_EXTRA_COLUMNS,
}


def _ensure_columns() -> None:
    """只补缺的列，已有数据原样保留；重复执行幂等。"""
    with connect() as conn:
        for table, columns in EXTRA_COLUMNS.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def insert_source(image_path: str, raw: Any) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO source (image_path, raw_json, created_at) VALUES (?, ?, ?)",
            (image_path, json.dumps(raw, ensure_ascii=False), time.time()),
        )
        return int(cur.lastrowid)


def insert_cards(source_id: int, cards: list[dict]) -> list[int]:
    ids: list[int] = []
    with connect() as conn:
        for card in cards:
            cur = conn.execute(
                """INSERT INTO card
                   (source_id, title, summary, subject, tags, formula, body, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_id,
                    card.get("title", "未命名知识点"),
                    card.get("summary", ""),
                    card.get("subject", ""),
                    ",".join(card.get("tags", [])),
                    card.get("formula", ""),
                    card.get("body", ""),
                    time.time(),
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def list_cards(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM card ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_card(card_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM card WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


# 允许人工修正的卡片字段：识别/萃取错了，只改内容，不改归属与时间戳
CARD_PATCH_FIELDS = ("title", "summary", "formula", "body")


def update_card(card_id: int, fields: dict) -> dict | None:
    """按字段局部更新，返回更新后的完整卡片；卡片不存在返回 None。"""
    allowed = {
        key: value
        for key, value in fields.items()
        if key in CARD_PATCH_FIELDS and value is not None
    }
    with connect() as conn:
        row = conn.execute("SELECT * FROM card WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            return None
        if allowed:
            assignments = ", ".join(f"{key} = ?" for key in allowed)
            conn.execute(
                f"UPDATE card SET {assignments} WHERE id = ?",
                (*allowed.values(), card_id),
            )
        updated = conn.execute(
            "SELECT * FROM card WHERE id = ?", (card_id,)
        ).fetchone()
    return dict(updated) if updated else None


def get_source(source_id: int) -> dict | None:
    """来源详情：raw_json 解析回版面块，便于原图与识别结果并排核对。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM source WHERE id = ?", (source_id,)
        ).fetchone()
    if row is None:
        return None
    source = dict(row)
    try:
        source["blocks"] = json.loads(source.get("raw_json") or "[]")
    except json.JSONDecodeError:
        source["blocks"] = []
    return source


def get_cards_by_source(source_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM card WHERE source_id = ? ORDER BY id ASC", (source_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# 原图列表默认返回条数
SOURCE_LIST_LIMIT = 50


def update_source_image(
    source_id: int, image_path: str, display_name: str, renamed_at: float | None
) -> None:
    """原图归档后同步路径与显示名；没改名成功时 renamed_at 传 None。"""
    with connect() as conn:
        conn.execute(
            "UPDATE source SET image_path = ?, display_name = ?, renamed_at = ? "
            "WHERE id = ?",
            (image_path, display_name, renamed_at, source_id),
        )


def list_sources(limit: int = SOURCE_LIST_LIMIT) -> list[dict]:
    """原图归档列表：按上传时间倒序，带每个来源的卡片数。

    display_name 是归档后的文件名；老数据没有该列值时退回原文件名。
    """
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.image_path, s.display_name, s.created_at,
                      COUNT(c.id) AS card_count
               FROM source s LEFT JOIN card c ON c.source_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC, s.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "display_name": row["display_name"] or Path(row["image_path"]).name,
            "created_at": row["created_at"],
            "card_count": int(row["card_count"]),
        }
        for row in rows
    ]


def get_cards(card_ids: list[int]) -> list[dict]:
    if not card_ids:
        return []
    placeholders = ",".join("?" for _ in card_ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM card WHERE id IN ({placeholders})", card_ids
        ).fetchall()
    return [dict(r) for r in rows]


def insert_questions(questions: list[dict]) -> list[int]:
    ids: list[int] = []
    with connect() as conn:
        for q in questions:
            cur = conn.execute(
                """INSERT INTO question
                   (card_ids, stem_latex, qtype, options, answer, solution,
                    difficulty, verified, verify_log, verify_level, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    json.dumps(q.get("card_ids", []), ensure_ascii=False),
                    q.get("stem_latex", ""),
                    q.get("qtype", "fill"),
                    json.dumps(q.get("options", []), ensure_ascii=False),
                    q.get("answer", ""),
                    q.get("solution", ""),
                    q.get("difficulty", "medium"),
                    1 if q.get("verified") else 0,
                    q.get("verify_log", ""),
                    q.get("verify_level", ""),
                    time.time(),
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def list_questions(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM question ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_decode_question(dict(row)) for row in rows]


# question 表里以 JSON 文本存储的列：出库时统一还原成数组
QUESTION_JSON_FIELDS = ("options", "card_ids")


def _decode_question(row: dict) -> dict:
    """options / card_ids 在库里是 JSON 文本，出库时还原成数组给前端。"""
    for field in QUESTION_JSON_FIELDS:
        value = row.get(field)
        if not isinstance(value, str):
            # 已是数组（或 None）就不二次解码，避免调用方重复解码时抛错
            continue
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            parsed = []
        row[field] = parsed if isinstance(parsed, list) else []
    return row


def get_question(question_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM question WHERE id = ?", (question_id,)
        ).fetchone()
    # 与 list_questions 同构：单题接口也返回数组，前端只有一套渲染逻辑
    return _decode_question(dict(row)) if row else None


def insert_attempt(question_id: int, user_answer: str, is_correct: bool,
                   error_reason: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO attempt
               (question_id, user_answer, is_correct, error_reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (question_id, user_answer, 1 if is_correct else 0, error_reason, time.time()),
        )
        return int(cur.lastrowid)


# 检索命中阈值：低于此分视为无关，避免硬凑出处
MIN_SCORE = 2.0
# 短查询（≤该长度）整串也参与匹配
WHOLE_QUERY_MAX_CHARS = 6
PUNCTUATION = r"[\s，。？！、；：,.?!;:'\"“”‘’（）()【】\[\]]"


def _query_tokens(query: str) -> list[str]:
    """中文无空格，靠 2-gram 补足；整串短查询额外加权。"""
    compact = re.sub(PUNCTUATION, "", query)
    if not compact:
        return []
    tokens: set[str] = {compact[i : i + 2] for i in range(len(compact) - 1)}
    if len(compact) <= WHOLE_QUERY_MAX_CHARS:
        tokens.add(compact)
    tokens.update(w for w in query.split() if w.isascii())
    return [t for t in tokens if t]


def search_cards(query: str, top_k: int) -> list[dict]:
    """轻量检索：字段加权计分，零依赖，够演示用。"""
    tokens = _query_tokens(query)
    if not tokens:
        return []

    with connect() as conn:
        rows = conn.execute("SELECT * FROM card").fetchall()

    scored: list[tuple[float, dict]] = []
    for row in rows:
        card = dict(row)
        title = card.get("title", "")
        summary = card.get("summary", "")
        body = card.get("body", "")
        tags = card.get("tags", "")
        score = 0.0
        for token in tokens:
            weight = 3.0 if len(token) >= 4 else 1.0
            if token in title:
                score += weight * 2.0
            if token in tags:
                score += weight * 1.5
            if token in summary:
                score += weight
            if token in body:
                score += weight
        if score >= MIN_SCORE:
            scored.append((score, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [card for _, card in scored[:top_k]]


def insert_event(run_id: str, agent: str, phase: str, payload: Any) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO agent_event (run_id, agent, phase, payload, ts)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, agent, phase, json.dumps(payload, ensure_ascii=False), time.time()),
        )


# ---------- 知识库统计与谱系 ----------

# 热度榜取前 N 名
STATS_TOP_N = 10


def bump_ask(card_ids: list[int]) -> None:
    """被追问命中的卡片计数 +1（知识点热度）。"""
    unique = sorted({int(i) for i in card_ids if i is not None})
    if not unique:
        return
    placeholders = ",".join("?" for _ in unique)
    with connect() as conn:
        conn.execute(
            f"UPDATE card SET ask_count = ask_count + 1, last_asked_at = ? "
            f"WHERE id IN ({placeholders})",
            [time.time(), *unique],
        )


def bump_question(card_ids: list[int]) -> None:
    """被用来出题的卡片计数 +1（知识点使用度）。"""
    unique = sorted({int(i) for i in card_ids if i is not None})
    if not unique:
        return
    placeholders = ",".join("?" for _ in unique)
    with connect() as conn:
        conn.execute(
            f"UPDATE card SET question_count = question_count + 1 WHERE id IN ({placeholders})",
            unique,
        )


def top_asked_cards(limit: int = STATS_TOP_N) -> list[dict]:
    """热度榜：只收被问过的卡片，按次数降序，同分再按出题次数、id 降序。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, ask_count, question_count FROM card "
            "WHERE ask_count > 0 "
            "ORDER BY ask_count DESC, question_count DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# 泛化标签阈值：出现在超过该比例卡片上的标签视为学科/主题级，不参与建边
# （如「二次函数」覆盖全部卡片，建边会把任意两张都连起来，画出来是一团毛线）
GENERIC_TAG_RATIO = 0.4
# 谱系图单次最多取多少张卡片，避免超大库拖慢接口
GRAPH_CARD_LIMIT = 500


def _card_tags(card: dict) -> list[str]:
    """标签去重且保序：同一卡片内重复标签不能生成自环。"""
    tags: list[str] = []
    for raw in (card.get("tags") or "").split(","):
        tag = raw.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def generic_tags(cards: list[dict]) -> set[str]:
    """占比超过 GENERIC_TAG_RATIO 的标签：学科/主题级，只展示不建边。"""
    if not cards:
        return set()
    counts: dict[str, int] = {}
    for card in cards:
        for tag in _card_tags(card):
            counts[tag] = counts.get(tag, 0) + 1
    return {tag for tag, n in counts.items() if n / len(cards) > GENERIC_TAG_RATIO}


def _fallback_edges(cards: list[dict]) -> list[dict]:
    """兜底：过滤后一条边都没有时，同一来源的卡片按 id 顺序串成链（N-1 条）。

    只保证图不是一堆孤点，边不带标签，权重固定为 1。
    """
    by_source: dict[Any, list[int]] = {}
    for card in cards:
        by_source.setdefault(card.get("source_id"), []).append(int(card["id"]))
    edges: list[dict] = []
    for ids in by_source.values():
        ordered = sorted(ids)
        for left, right in zip(ordered, ordered[1:]):
            edges.append({"source": left, "target": right, "weight": 1, "tags": []})
    return edges


def graph_data() -> dict:
    """知识谱系：节点=知识点卡片，边=共享「非泛化」标签，权重=共享标签数。"""
    cards = list_cards(limit=GRAPH_CARD_LIMIT)
    generic = generic_tags(cards)
    nodes: list[dict] = []
    tag_members: dict[str, list[int]] = {}

    for card in cards:
        tags = _card_tags(card)
        card_id = int(card["id"])
        nodes.append(
            {
                "id": card_id,
                "title": card.get("title", ""),
                "summary": card.get("summary", ""),
                "subject": card.get("subject", ""),
                "tags": tags,
                "ask_count": int(card.get("ask_count") or 0),
                "question_count": int(card.get("question_count") or 0),
            }
        )
        for tag in tags:
            if tag in generic:
                continue
            tag_members.setdefault(tag, []).append(card_id)

    edges: dict[tuple[int, int], dict] = {}
    strong_ids = {card_id for ids in tag_members.values() for card_id in ids}
    for tag, ids in tag_members.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if a == b:
                    continue
                key = (a, b)
                if key in edges:
                    edges[key]["weight"] += 1
                    edges[key]["tags"].append(tag)
                else:
                    edges[key] = {"source": a, "target": b, "weight": 1, "tags": [tag]}

    edge_list = [
        edge
        for edge in edges.values()
        # 强关联：两端都必须带至少一个非泛化标签（只从 tag_members 建边天然满足，显式兜一层）
        if edge["weight"] >= 1 and {edge["source"], edge["target"]} <= strong_ids
    ]
    if not edge_list:
        edge_list = _fallback_edges(cards)

    return {
        "nodes": nodes,
        "edges": edge_list,
        "tags": sorted({tag for card in cards for tag in _card_tags(card)}),
    }
