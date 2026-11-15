"""原图归档纯函数：文件名清洗、重名后缀、按识别标题改名。"""

from __future__ import annotations

import time
from pathlib import Path

from app import storage

CHINESE_TITLE = "二次函数的图象与性质"
# 固定测试时间：2026-11-15 10:00 本地时区
FIXED_TS = time.mktime((2026, 11, 15, 10, 0, 0, 0, 0, -1))
FIXED_DATE = "2026-11-15"


def _png(tmp_path: Path, name: str = "uuid.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return path


def test_slugify_keeps_chinese_title():
    assert storage.slugify(CHINESE_TITLE) == CHINESE_TITLE


def test_slugify_folds_whitespace_to_dash():
    assert storage.slugify("  二次函数   对称轴  ") == "二次函数-对称轴"
    assert storage.slugify("a\tb\nc") == "a-b-c"


def test_slugify_strips_illegal_chars():
    raw = '第1章: y=x^2? <草稿>|*/\\"'
    assert storage.slugify(raw) == "第1章-y=x^2-草稿"


def test_slugify_truncates_long_titles():
    result = storage.slugify("长" * (storage.MAX_NAME_CHARS + 10))
    assert result == "长" * storage.MAX_NAME_CHARS


def test_slugify_truncation_drops_trailing_dash():
    result = storage.slugify("长" * 39 + " - 尾")
    assert result == "长" * 39


def test_slugify_returns_empty_for_empty_or_invalid_only():
    assert storage.slugify("") == ""
    assert storage.slugify("   ") == ""
    assert storage.slugify('\\/:*?"<>|') == ""


def test_unique_path_returns_same_when_free(tmp_path):
    path = tmp_path / "空闲.png"
    assert storage.unique_path(path) == path


def test_unique_path_appends_suffix_on_collision(tmp_path):
    first = _png(tmp_path, "2026-11-15-标题.png")
    second = storage.unique_path(first)
    assert second.name == "2026-11-15-标题-2.png"

    second.write_bytes(b"x")
    third = storage.unique_path(first)
    assert third.name == "2026-11-15-标题-3.png"


def test_rename_after_ocr_uses_date_and_title(tmp_path):
    image = _png(tmp_path)

    renamed = storage.rename_after_ocr(image, CHINESE_TITLE, when=FIXED_TS)

    assert renamed.name == f"{FIXED_DATE}-{CHINESE_TITLE}.png"
    assert renamed.parent == tmp_path
    assert renamed.is_file()
    assert not image.exists()


def test_rename_after_ocr_dedupes_repeated_titles(tmp_path):
    names = []
    for index in range(3):
        image = _png(tmp_path, f"uuid-{index}.png")
        names.append(storage.rename_after_ocr(image, CHINESE_TITLE, when=FIXED_TS).name)

    assert names == [
        f"{FIXED_DATE}-{CHINESE_TITLE}.png",
        f"{FIXED_DATE}-{CHINESE_TITLE}-2.png",
        f"{FIXED_DATE}-{CHINESE_TITLE}-3.png",
    ]


def test_rename_after_ocr_keeps_uuid_name_without_title(tmp_path):
    image = _png(tmp_path)

    assert storage.rename_after_ocr(image, "", when=FIXED_TS) == image
    assert image.is_file()


def test_rename_after_ocr_keeps_missing_file_untouched(tmp_path):
    missing = tmp_path / "gone.png"
    assert storage.rename_after_ocr(missing, CHINESE_TITLE, when=FIXED_TS) == missing


def test_pick_title_prefers_title_block():
    blocks = [
        {"type": "text", "content": "先复习一般式"},
        {"type": "title", "content": CHINESE_TITLE},
    ]
    cards = [{"title": "卡片标题"}]
    assert storage.pick_title(blocks, cards) == CHINESE_TITLE


def test_pick_title_falls_back_to_first_card():
    assert storage.pick_title([], [{"title": "卡片标题"}, {"title": "第二张"}]) == "卡片标题"


def test_pick_title_without_any_title():
    assert storage.pick_title([], []) == ""
    assert storage.pick_title([{"type": "title", "content": "   "}], [{}]) == ""
