"""reset_demo：默认保留归档原图，只有 --purge-images 才删文件。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app import config, db
from app.providers import fixtures

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reset_demo.py"
QUESTION = {
    "stem_latex": "1 + 1 = ?",
    "qtype": "fill",
    "options": [],
    "answer": "2",
    "verified": True,
}


def _load_reset_module():
    spec = importlib.util.spec_from_file_location("reset_demo", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed() -> Path:
    image = config.UPLOAD_DIR / "2026-11-15-二次函数的图象与性质.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    source_id = db.insert_source(str(image), fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)
    db.insert_questions([QUESTION])
    return image


def test_reset_keeps_images_by_default(capsys):
    reset = _load_reset_module()
    image = _seed()

    assert reset.main([]) == 0

    assert image.is_file()
    assert db.list_cards() == []
    assert db.list_questions() == []
    assert "--purge-images" in capsys.readouterr().out


def test_reset_purge_images_removes_archived_files(capsys):
    reset = _load_reset_module()
    image = _seed()

    assert reset.main(["--purge-images"]) == 0

    assert not image.exists()
    assert db.list_cards() == []
    assert "已删除 1 张原图" in capsys.readouterr().out


def test_reset_does_not_touch_subdirectories(tmp_path):
    reset = _load_reset_module()
    _seed()
    nested = config.UPLOAD_DIR / "keep" / "note.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("keep", encoding="utf-8")

    assert reset.main(["--purge-images"]) == 0

    assert nested.is_file()
