"""SQLite 存储层：每表职责单一，写入函数只做一件事。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
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
                    difficulty, verified, verify_log, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


def _decode_question(row: dict) -> dict:
    """options / card_ids 在库里是 JSON 文本，出库时还原成数组给前端。"""
    row["options"] = json.loads(row.get("options") or "[]")
    row["card_ids"] = json.loads(row.get("card_ids") or "[]")
    return row


def get_question(question_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM question WHERE id = ?", (question_id,)
        ).fetchone()
    return dict(row) if row else None


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
