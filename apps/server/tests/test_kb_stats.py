"""计数原语与老库迁移：空列表/不存在的 id 不报错，旧 schema 补列不丢数据。"""

from __future__ import annotations

import sqlite3

import pytest

from app import db
from app.providers import fixtures

MISSING_ID = 99999

# 迁移前的老 card 表：没有 ask_count / question_count / last_asked_at
LEGACY_SCHEMA = """
CREATE TABLE card (
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
"""

LEGACY_CARDS = [
    ("老卡片一", "旧库里的第一张卡片", "数学", "二次函数,一般式"),
    ("老卡片二", "旧库里的第二张卡片", "数学", "二次函数,配方"),
]


def _seed_cards() -> list[dict]:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)
    return db.list_cards()


def test_bump_ask_increments_each_call():
    cards = _seed_cards()
    target = cards[0]["id"]

    db.bump_ask([target])
    db.bump_ask([target])

    stored = db.get_card(target)
    assert stored["ask_count"] == 2
    assert stored["last_asked_at"] is not None
    assert all(db.get_card(c["id"])["ask_count"] == 0 for c in cards[1:])


def test_bump_ask_ignores_empty_and_missing_ids():
    _seed_cards()
    db.bump_ask([])
    db.bump_ask([MISSING_ID])
    assert all(card["ask_count"] == 0 for card in db.list_cards())


def test_bump_question_increments_once_per_card():
    cards = _seed_cards()
    ids = [card["id"] for card in cards]

    db.bump_question(ids)
    # 同一张卡片重复出现也只累加一次
    db.bump_question([ids[0], ids[0]])

    assert db.get_card(ids[0])["question_count"] == 2
    assert all(db.get_card(i)["question_count"] == 1 for i in ids[1:])


def test_bump_question_ignores_empty_and_missing_ids():
    _seed_cards()
    db.bump_question([])
    db.bump_question([MISSING_ID])
    assert all(card["question_count"] == 0 for card in db.list_cards())


def test_top_asked_cards_sorted_and_limited():
    cards = _seed_cards()
    for repeat, card in enumerate(cards):
        for _ in range(repeat):
            db.bump_ask([card["id"]])

    top = db.top_asked_cards(limit=2)
    assert [row["id"] for row in top] == [cards[-1]["id"], cards[-2]["id"]]
    assert top[0]["ask_count"] > top[1]["ask_count"]
    assert set(top[0]) == {"id", "title", "ask_count", "question_count"}


def _build_legacy_db(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
        for title, summary, subject, tags in LEGACY_CARDS:
            conn.execute(
                "INSERT INTO card (source_id, title, summary, subject, tags, formula,"
                " body, created_at) VALUES (?, ?, ?, ?, ?, '', '', ?)",
                (1, title, summary, subject, tags, 1.0),
            )


def test_init_db_migrates_legacy_schema_without_data_loss(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.db"
    _build_legacy_db(legacy_path)
    monkeypatch.setattr(db, "DB_PATH", legacy_path)

    db.init_db()
    db.init_db()  # 再跑一次必须幂等

    with sqlite3.connect(legacy_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(card)")}
    assert set(db.CARD_EXTRA_COLUMNS) <= columns

    cards = db.list_cards()
    assert [card["title"] for card in cards] == [c[0] for c in reversed(LEGACY_CARDS)]
    assert all(card["ask_count"] == 0 for card in cards)
    assert all(card["question_count"] == 0 for card in cards)
    assert all(card["last_asked_at"] is None for card in cards)

    # 迁移后计数可用
    db.bump_ask([cards[0]["id"]])
    db.bump_question([cards[0]["id"]])
    migrated = db.get_card(cards[0]["id"])
    assert (migrated["ask_count"], migrated["question_count"]) == (1, 1)


def test_init_db_keeps_legacy_card_content(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy-content.db"
    _build_legacy_db(legacy_path)
    monkeypatch.setattr(db, "DB_PATH", legacy_path)

    db.init_db()

    stored = db.get_card(1)
    assert stored["summary"] == LEGACY_CARDS[0][1]
    assert stored["tags"] == LEGACY_CARDS[0][3]


@pytest.mark.parametrize("card_ids", [[], [MISSING_ID], [None]])
def test_bump_functions_tolerate_odd_input(card_ids):
    _seed_cards()
    db.bump_ask(card_ids)
    db.bump_question(card_ids)
    assert all(card["ask_count"] == 0 for card in db.list_cards())
