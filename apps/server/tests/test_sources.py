"""原图归档接口：列表、重命名后仍能取图、老库补列不丢数据。"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, storage
from app.events import EventBus
from app.main import app
from app.orchestrator import Orchestrator
from app.providers import fixtures

MISSING_ID = 99999
RUN_ID = "test-ingest"
CHINESE_TITLE = "二次函数的图象与性质"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake"
# 归档文件名：<YYYY-MM-DD>-<标题>.png
ARCHIVE_NAME = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-{CHINESE_TITLE}\.png")


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_sources_list_is_newest_first_with_card_count(client):
    older = db.insert_source("older.png", [])
    newer = db.insert_source("newer.png", [])
    db.insert_cards(newer, fixtures.MOCK_CARDS[:2])

    body = client.get("/api/sources").json()
    rows = body["sources"]

    assert [row["id"] for row in rows] == [newer, older]
    assert set(rows[0]) == {
        "id",
        "display_name",
        "created_at",
        "card_count",
        "image_url",
    }
    assert rows[0]["card_count"] == 2
    assert rows[0]["image_url"] == f"/api/sources/{newer}/image"
    # 没归档过的老数据退回原文件名
    assert rows[1]["display_name"] == "older.png"
    assert rows[1]["card_count"] == 0


def test_sources_list_empty(client):
    assert client.get("/api/sources").json() == {"sources": []}


def test_image_endpoint_still_works_after_rename(client, tmp_path):
    image = tmp_path / "uuid.png"
    image.write_bytes(IMAGE_BYTES)
    source_id = db.insert_source(str(image), fixtures.MOCK_BLOCKS)

    archived = storage.rename_after_ocr(image, CHINESE_TITLE)
    db.update_source_image(source_id, str(archived), archived.name, time.time())

    response = client.get(f"/api/sources/{source_id}/image")
    assert response.status_code == 200
    assert response.content == IMAGE_BYTES

    detail = client.get(f"/api/sources/{source_id}").json()
    assert detail["display_name"] == archived.name
    assert "image_path" not in detail


def test_image_endpoint_missing_source(client):
    assert client.get(f"/api/sources/{MISSING_ID}/image").status_code == 404


def test_source_file_uses_document_mime(client, tmp_path):
    pdf = tmp_path / "讲义.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    source_id = db.insert_source(str(pdf), [])

    response = client.get(f"/api/sources/{source_id}/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def test_ingest_archives_image_by_recognized_title():
    image = config.UPLOAD_DIR / "uuid.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(IMAGE_BYTES)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ingest(RUN_ID, image))

    history = bus.get(RUN_ID).history
    assert [event for event in history if event.phase == "error"] == []

    files = [path.name for path in config.UPLOAD_DIR.iterdir() if path.is_file()]
    assert len(files) == 1
    assert ARCHIVE_NAME.fullmatch(files[0]), files

    sources = db.list_sources()
    assert len(sources) == 1
    assert sources[0]["display_name"] == files[0]
    assert sources[0]["card_count"] == len(fixtures.MOCK_CARDS)

    stored = db.get_source(sources[0]["id"])
    assert stored["renamed_at"] is not None
    assert stored["image_path"] == str(config.UPLOAD_DIR / files[0])

    response = TestClient(app).get(f"/api/sources/{sources[0]['id']}/image")
    assert response.status_code == 200
    assert response.content == IMAGE_BYTES


def test_ingest_keeps_uuid_name_when_recognition_has_no_title(monkeypatch):
    monkeypatch.setattr(fixtures, "MOCK_BLOCKS", [{"type": "text", "content": "没有标题"}])
    monkeypatch.setattr(fixtures, "MOCK_CARDS", [])
    image = config.UPLOAD_DIR / "uuid.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(IMAGE_BYTES)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ingest(RUN_ID, image))

    assert image.is_file()
    sources = db.list_sources()
    assert sources[0]["display_name"] == "uuid.png"


LEGACY_SOURCE_SCHEMA = """
CREATE TABLE source (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  image_path TEXT    NOT NULL,
  raw_json   TEXT    NOT NULL,
  created_at REAL    NOT NULL
);
"""


def test_init_db_adds_source_columns_without_data_loss(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy-source.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript(LEGACY_SOURCE_SCHEMA)
        conn.execute(
            "INSERT INTO source (image_path, raw_json, created_at) VALUES (?, ?, ?)",
            ("old-board.png", "[]", 1.0),
        )

    monkeypatch.setattr(db, "DB_PATH", legacy_path)
    db.init_db()
    db.init_db()  # 幂等

    with sqlite3.connect(legacy_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    assert set(db.SOURCE_EXTRA_COLUMNS) <= columns

    source = db.get_source(1)
    assert source["image_path"] == "old-board.png"
    assert source["display_name"] is None
    assert source["renamed_at"] is None
    assert source["blocks"] == []


def test_legacy_source_appears_in_list_with_file_name(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy-list.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript(LEGACY_SOURCE_SCHEMA)
        conn.execute(
            "INSERT INTO source (image_path, raw_json, created_at) VALUES (?, ?, ?)",
            ("C:/old/board.png", "[]", 1.0),
        )

    monkeypatch.setattr(db, "DB_PATH", legacy_path)
    db.init_db()

    rows = db.list_sources()
    assert len(rows) == 1
    assert rows[0]["display_name"] == Path("C:/old/board.png").name
    assert rows[0]["card_count"] == 0
