"""文档输入：DOCX / TXT / MD 结构化，PDF 文本质量判定与双路径。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pymupdf
from docx import Document

from app import config, db, documents
from app.events import EventBus
from app.orchestrator import Orchestrator

RUN_ID = "test-doc-ingest"
# 正常中文样本：每页可读字数远超 40
GOOD_PAGE = "二次函数的对称轴是 x = -b/(2a)，顶点坐标落在对称轴上，开口方向由 a 的符号决定。" * 2
# 实测乱码形态：中文字体子集缺 ToUnicode 时 pypdf 的输出
GARBLED_PAGE = "6¶: O\x7fyÒ: ⁄3⁄4 6¶: O\x7fyÒ: ⁄3⁄4"
ENGLISH_PAGE = "The axis of symmetry of a quadratic function is x = -b / (2a). " * 2


def _make_pdf(path, pages: int = 1) -> None:
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page(width=612, height=792)
    document.save(str(path))
    document.close()


def _make_docx(path) -> None:
    document = Document()
    document.add_heading("二次函数的图象与性质", level=1)
    document.add_paragraph("一般式 y = ax² + bx + c，其中 a 不等于 0。")
    document.add_paragraph(r"配方得 $y = a(x + \frac{b}{2a})^2 + \frac{4ac-b^2}{4a}$。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "开口方向"
    table.cell(0, 1).text = "a 的符号"
    table.cell(1, 0).text = "向上"
    table.cell(1, 1).text = "a > 0"
    document.save(str(path))


# ---- DOCX ----

def test_docx_blocks_keep_types_and_document_order(tmp_path):
    path = tmp_path / "讲义.docx"
    _make_docx(path)

    blocks = asyncio.run(documents.read_blocks(path))

    assert [block["type"] for block in blocks] == ["title", "text", "formula", "table"]


def test_docx_table_cells_joined_with_pipe(tmp_path):
    path = tmp_path / "讲义.docx"
    _make_docx(path)

    table = asyncio.run(documents.read_blocks(path))[-1]

    assert table["content"] == "开口方向 | a 的符号\n向上 | a > 0"


def test_docx_blocks_carry_latex_and_content_keys(tmp_path):
    path = tmp_path / "讲义.docx"
    _make_docx(path)

    for block in asyncio.run(documents.read_blocks(path)):
        assert set(block) == {"type", "content", "latex"}
        assert block["content"].strip()


# ---- TXT / MD ----

def test_txt_splits_paragraphs_by_blank_line(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("第一段第一行\n第一段第二行\n\n第二段\n", encoding="utf-8")

    blocks = asyncio.run(documents.read_blocks(path))

    assert [block["type"] for block in blocks] == ["text", "text"]
    assert blocks[0]["content"] == "第一段第一行 第一段第二行"
    assert blocks[1]["content"] == "第二段"


def test_md_headings_become_title_blocks(tmp_path):
    path = tmp_path / "note.md"
    path.write_text(
        "# 二次函数\n\n对称轴 x = -b/(2a)。\n\n## 顶点式\n\n$y = (x-2)^2 - 1$\n",
        encoding="utf-8",
    )

    blocks = asyncio.run(documents.read_blocks(path))

    assert [block["type"] for block in blocks] == ["title", "text", "title", "formula"]
    assert [block["content"] for block in blocks][:2] == ["二次函数", "对称轴 x = -b/(2a)。"]


def test_unknown_document_suffix_raises(tmp_path):
    path = tmp_path / "note.rtf"
    path.write_text("x", encoding="utf-8")

    try:
        asyncio.run(documents.read_blocks(path))
    except ValueError as exc:
        assert "不支持的文档类型" in str(exc)
    else:  # pragma: no cover - 走到这里说明没有拒绝未知格式
        raise AssertionError("未知后缀应当报错")


# ---- PDF 文本质量判定 ----

def test_garbled_pdf_text_is_rejected():
    assert documents.is_text_usable([GARBLED_PAGE] * 3) is False


def test_control_characters_are_rejected():
    polluted = "正常的二次函数笔记。" * 20 + "\x00" * 20
    assert documents.is_text_usable([polluted]) is False


def test_normal_chinese_text_is_accepted():
    assert documents.is_text_usable([GOOD_PAGE, GOOD_PAGE]) is True


def test_plain_english_text_is_accepted():
    assert documents.is_text_usable([ENGLISH_PAGE]) is True


def test_short_pages_are_rejected():
    assert documents.is_text_usable([GOOD_PAGE, "只有十几个字"]) is False


def test_empty_text_layer_is_rejected():
    assert documents.is_text_usable([]) is False
    assert documents.is_text_usable([""]) is False


# ---- PDF 双路径 ----

def test_pdf_text_layer_skips_vision(tmp_path, monkeypatch):
    path = tmp_path / "good.pdf"
    _make_pdf(path)
    monkeypatch.setattr(documents, "extract_pdf_text", lambda _path: [GOOD_PAGE, GOOD_PAGE])

    async def must_not_call(_image_path):
        raise AssertionError("文本层可用时不应调视觉模型")

    monkeypatch.setattr(documents.vlm, "recognize", must_not_call)

    blocks, note = asyncio.run(documents.read_document(path))

    assert blocks and all(block["type"] in {"text", "formula"} for block in blocks)
    assert "文本层可用，直接解析" in note
    assert "2 页" in note


def test_pdf_garbled_text_renders_pages_for_vision(tmp_path, monkeypatch):
    path = tmp_path / "garbled.pdf"
    _make_pdf(path, pages=5)
    monkeypatch.setattr(documents, "extract_pdf_text", lambda _path: [GARBLED_PAGE] * 5)

    seen: list = []

    async def fake_recognize(image_path):
        image_path = Path(image_path)
        assert image_path.is_file()
        assert image_path.suffix == ".png"
        seen.append(image_path)
        return [{"type": "text", "content": "渲染页", "latex": ""}]

    monkeypatch.setattr(documents.vlm, "recognize", fake_recognize)

    blocks, note = asyncio.run(documents.read_document(path))

    assert len(seen) == config.MAX_DOC_PAGES == 3
    assert len(blocks) == 3
    assert "文档共 5 页" in note
    assert "取前 3 页" in note


def _png_size(path) -> tuple[int, int]:
    """直接读 PNG 头（IHDR）里的像素宽高，避免图像库自己换算 DPI。"""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_render_pdf_pages_respects_limit_and_dpi(tmp_path):
    path = tmp_path / "pages.pdf"
    _make_pdf(path, pages=5)

    images = documents.render_pdf_pages(path, 2, tmp_path / "render")

    assert [image.name for image in images] == ["page-1.png", "page-2.png"]
    width, height = _png_size(images[0])
    assert width == round(612 / 72 * documents.RENDER_DPI)
    assert height == round(792 / 72 * documents.RENDER_DPI)


# ---- 截断保护 ----

def test_long_document_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_DOC_CHARS", 50)
    path = tmp_path / "long.md"
    path.write_text("# 标题\n\n" + "这一段很长。" * 100, encoding="utf-8")

    blocks = asyncio.run(documents.read_blocks(path))

    assert sum(len(block["content"]) for block in blocks) <= 50


def test_truncate_blocks_keeps_block_shape(tmp_path):
    blocks = [
        {"type": "text", "content": "一二三四五", "latex": ""},
        {"type": "text", "content": "六七八九十", "latex": ""},
    ]
    kept = documents.truncate_blocks(blocks)
    assert kept == blocks


# ---- 流程接入 ----

def test_ingest_txt_reports_text_path_and_keeps_extension():
    path = config.UPLOAD_DIR / "uuid.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 二次函数的图象与性质\n\n对称轴 x = -b/(2a)。", encoding="utf-8")

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ingest(RUN_ID, path))

    history = bus.get(RUN_ID).history
    assert [event for event in history if event.phase == "error"] == []

    notes = [event.message for event in history if event.agent == "recognize"]
    assert any("文本直接解析" in message for message in notes)

    files = [item.name for item in config.UPLOAD_DIR.iterdir() if item.is_file()]
    assert len(files) == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-二次函数的图象与性质\.txt", files[0])
    assert db.list_sources()[0]["display_name"] == files[0]
