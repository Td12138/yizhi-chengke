"""mock 样例集：按文件名分流，三套内容互不重复、同名字结果确定。"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from fastapi import UploadFile

from app import config, db, main as main_module
from app.events import EventBus
from app.orchestrator import Orchestrator
from app.providers import fixtures, vlm

RUN_ID = "test-mock-sets"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake"
QUADRATIC = "sample-quadratic.png"
EQUATION = "sample-equation.png"
TRIG = "sample-trig.png"
UNKNOWN = "uuid.png"


def test_english_sample_names_hit_their_own_sets():
    assert fixtures.detect_set(QUADRATIC) == "quadratic"
    assert fixtures.detect_set(EQUATION) == "equation"
    assert fixtures.detect_set(TRIG) == "trig"


def test_repo_sample_names_and_exam_pdf_hit_too():
    assert fixtures.detect_set("01-二次函数图象与性质.png") == "quadratic"
    assert fixtures.detect_set("02-一元二次方程求根.png") == "equation"
    assert fixtures.detect_set("03-三角函数图像要点.png") == "trig"
    assert fixtures.detect_set("sample-exam.pdf") == "equation"


def test_unknown_name_falls_back_to_default():
    assert fixtures.detect_set(UNKNOWN) is None
    assert fixtures.detect_set("") is None
    assert fixtures.match_note(UNKNOWN) == "未匹配到样例集，使用默认"
    assert fixtures.blocks_for(UNKNOWN) == fixtures.MOCK_BLOCKS
    assert fixtures.cards_for(UNKNOWN) == fixtures.MOCK_CARDS


def test_blocks_and_cards_come_from_the_same_set():
    for name, key in (
        (QUADRATIC, "quadratic"),
        (EQUATION, "equation"),
        (TRIG, "trig"),
    ):
        blocks = asyncio.run(vlm.recognize(Path(name)))
        cards = fixtures.cards_for(name)
        assert blocks == fixtures.SETS[key]["blocks"]
        assert cards == fixtures.SETS[key]["cards"]
        assert blocks[0]["type"] == "title"


def test_same_filename_is_deterministic():
    assert asyncio.run(vlm.recognize(Path(TRIG))) == asyncio.run(
        vlm.recognize(Path(TRIG))
    )
    assert fixtures.cards_for(EQUATION) == fixtures.cards_for(EQUATION)


def test_card_titles_are_unique_across_sets():
    titles = [
        card["title"] for sample in fixtures.SETS.values() for card in sample["cards"]
    ]
    assert len(titles) == len(set(titles))
    assert set(fixtures.SET_LABELS) == set(fixtures.SETS)


def test_each_set_has_three_or_four_cards():
    for sample in fixtures.SETS.values():
        assert 3 <= len(sample["cards"]) <= 4
        assert all(
            {"title", "summary", "subject", "tags", "formula", "body"} <= set(card)
            for card in sample["cards"]
        )


def _ingest(name: str, source_name: str | None = None):
    image = config.UPLOAD_DIR / name
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(IMAGE_BYTES)
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ingest(RUN_ID, image, source_name))
    return bus.get(RUN_ID).history


def _stored_titles() -> list[str]:
    """卡片按入库顺序返回（list_cards 是 id 倒序，这里翻回来）。"""
    return [card["title"] for card in reversed(db.list_cards())]


def test_ingest_reports_matched_set_and_uses_its_cards():
    events = _ingest("uuid-trig.png", TRIG)

    assert [event for event in events if event.phase == "error"] == []
    notes = [event.message for event in events if event.agent == "recognize"]
    assert any("命中样例集：三角函数" in message for message in notes)

    assert _stored_titles() == [card["title"] for card in fixtures.SETS["trig"]["cards"]]


def test_ingest_reports_default_fallback():
    events = _ingest(UNKNOWN)

    notes = [event.message for event in events if event.agent == "recognize"]
    assert any("未匹配到样例集，使用默认" in message for message in notes)
    assert _stored_titles() == [card["title"] for card in fixtures.MOCK_CARDS]


def test_upload_passes_original_filename_to_ingest(monkeypatch):
    captured: dict = {}

    async def fake_ingest(run_id, path, source_name=None):
        captured["source_name"] = source_name

    monkeypatch.setattr(main_module.orchestrator, "ingest", fake_ingest)

    async def call():
        upload = UploadFile(filename=TRIG, file=io.BytesIO(IMAGE_BYTES))
        response = await main_module.ingest(upload)
        await asyncio.sleep(0)  # 让 spawn 出去的任务跑起来
        return response

    response = asyncio.run(call())

    assert response["kind"] == "image"
    assert captured["source_name"] == TRIG
