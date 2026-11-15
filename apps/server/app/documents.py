"""文档输入：PDF / DOCX / TXT / MD → 与视觉模型一致的版面块结构。

PDF 必须双路径：中文 PDF 常见的字体子集缺 ToUnicode，`pypdf` 抽出来是
`6¶: O\x7fyÒ: ⁄3...` 这种乱码，所以先做文本质量判定——
可读字符占比够高且每页够长就直接解析，否则用 PyMuPDF 渲染成图交给视觉模型，
最多渲染 `MAX_DOC_PAGES` 页。

入口是异步的：渲染路径要逐页 `await vlm.recognize(...)`。
只要解析结果、不关心走哪条路径时用 `read_blocks`，要写事件日志用 `read_document`
（多返回一句给人看的说明）。
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from . import config
from .providers import fixtures, vlm

# 可读字符：中日韩 + 日文假名 + 韩文 + 希腊字母 + 拉丁字母 + 数字
# 希腊字母单列：数学 PDF 的 α / β / θ 很常见，漏掉会把正常文本层判成乱码
READABLE_CHARS = re.compile(
    r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0370-\u03ffA-Za-z0-9]"
)
# 乱码特征：控制符、替换字符，以及缺 ToUnicode 时最常见的拉丁扩展字符
GARBLED_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f¶ÒÐÞþ\ufffd]")
# 文本层可用阈值：整体可读占比、单页最少可读字数、乱码占比上限
MIN_READABLE_RATIO = 0.5
MIN_CHARS_PER_PAGE = 40
MAX_GARBLED_RATIO = 0.05
# 允许少量页（封面 / 空白页 / 插图页）没有文本：可用页占比达到该值即走直读
MIN_USABLE_PAGE_RATIO = 0.8
# PDF 只抽前 N 页文本：几百页的扫描件逐页抽文本会白等几分钟
MAX_PDF_TEXT_PAGES = 200
# DOCX 解压后总大小上限：zip 炸弹可以几百 KB 压出几 GB，解析前先看声明大小
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
# 渲染 DPI：够视觉模型认清公式，又不至于把 PNG 撑到几十 MB
RENDER_DPI = 180
# 渲染产物目录前缀（临时目录，识别完即删）
RENDER_TMP_PREFIX = "yizhi-pdf-"

# 段落内部换行与多余空白压成一个空格
WHITESPACE = re.compile(r"\s+")
# 行内公式与常见 LaTeX 命令：命中即按 formula 块处理
INLINE_MATH = re.compile(r"\$[^$]+\$")
FORMULA_MARKERS = ("\\frac", "\\dfrac", "\\sqrt", "\\sum", "\\int", "\\Delta", "^{", "_{")
# 标题样式前缀：英文模板 Heading / Title，中文模板「标题」
TITLE_STYLE_PREFIXES = ("Heading", "Title", "标题")
# 纯文本（txt/md）解码顺序：先 UTF-8（含 BOM），再按中文 Windows 常见的 GB18030 兜底
TEXT_ENCODINGS = ("utf-8-sig", "gb18030")


def _block(block_type: str, content: str, latex: str = "") -> dict:
    """与 VLM 输出同构的版面块。"""
    return {"type": block_type, "content": content, "latex": latex}


def readable_ratio(text: str) -> float:
    """可读字符（中日韩 + 拉丁字母 + 数字）占比；空串算 0。"""
    if not text:
        return 0.0
    return len(READABLE_CHARS.findall(text)) / len(text)


def garbled_ratio(text: str) -> float:
    """乱码字符占比：控制符、替换字符、¶Ò 这类缺 ToUnicode 的典型产物。"""
    if not text:
        return 0.0
    return len(GARBLED_CHARS.findall(text)) / len(text)


def is_text_usable(pages: list[str]) -> bool:
    """PDF 文本层能否直接用：可用页占比够高，整体可读够高、乱码够少。

    不再要求每一页都够长：真实 PDF 常有空白页 / 整页插图，卡死在「每页 ≥40 字」
    会把本来能直读的文档全部推去渲染，白白多花几倍时间。
    """
    if not pages:
        return False
    joined = "\n".join(pages)
    if readable_ratio(joined) < MIN_READABLE_RATIO:
        return False
    if garbled_ratio(joined) > MAX_GARBLED_RATIO:
        return False
    usable = sum(
        1
        for page in pages
        if len(READABLE_CHARS.findall(page)) >= MIN_CHARS_PER_PAGE
    )
    return usable / len(pages) >= MIN_USABLE_PAGE_RATIO


def _is_formula(text: str) -> bool:
    """含 $...$ 行内公式或常见 LaTeX 命令的段落按公式块处理。"""
    return bool(INLINE_MATH.search(text)) or any(
        marker in text for marker in FORMULA_MARKERS
    )


def truncate_blocks(blocks: list[dict]) -> list[dict]:
    """按 MAX_DOC_CHARS 截断：保留前面的块，最后一块可截短。"""
    remaining = config.MAX_DOC_CHARS
    kept: list[dict] = []
    for block in blocks:
        content = str(block.get("content", ""))
        if remaining <= 0:
            break
        if len(content) > remaining:
            block = {**block, "content": content[:remaining]}
        kept.append(block)
        remaining -= len(block["content"])
    return kept


def text_blocks(text: str, mark_headings: bool = False) -> list[dict]:
    """纯文本 → 段落块：空行分段，段内换行压成空格。

    mark_headings=True 时（txt / md），`#` 开头的行单独成 title 块。
    """
    blocks: list[dict] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        content = WHITESPACE.sub(" ", " ".join(buffer)).strip()
        buffer.clear()
        if content:
            blocks.append(
                _block("formula" if _is_formula(content) else "text", content)
            )

    for line in text.splitlines():
        stripped = line.strip()
        if mark_headings and stripped.startswith("#"):
            flush()
            title = stripped.strip("# ").strip()
            if title:
                blocks.append(_block("title", title))
            continue
        if not stripped:
            flush()
            continue
        buffer.append(stripped)
    flush()
    return blocks


def _read_text_file(path: Path) -> str:
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文本编码：{path.name}")


def _iter_docx_items(document: Any) -> Iterator[Paragraph | Table]:
    """按原文顺序产出段落与表格（`document.paragraphs` 会把表格顺序丢掉）。"""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _docx_paragraph_block(paragraph: Paragraph) -> dict | None:
    content = WHITESPACE.sub(" ", paragraph.text).strip()
    if not content:
        return None
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    if style.startswith(TITLE_STYLE_PREFIXES):
        return _block("title", content)
    return _block("formula" if _is_formula(content) else "text", content)


def _docx_table_block(table: Table) -> dict | None:
    rows = [
        " | ".join(WHITESPACE.sub(" ", cell.text).strip() for cell in row.cells)
        for row in table.rows
    ]
    content = "\n".join(rows).strip()
    return _block("table", content) if content else None


def _check_docx_archive(path: Path) -> None:
    """解析前先看 zip 目录里声明的解压后大小，挡住 zip 炸弹与损坏包。"""
    try:
        with zipfile.ZipFile(path) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("DOCX 无法解析：文件损坏或已加密") from exc
    if total > MAX_DOCX_UNCOMPRESSED_BYTES:
        limit_mb = MAX_DOCX_UNCOMPRESSED_BYTES // (1024 * 1024)
        raise ValueError(f"DOCX 解压后超过 {limit_mb} MB，已拒绝")


def docx_blocks(path: Path) -> list[dict]:
    """DOCX：标题样式 → title，含公式的段落 → formula，表格 → table，顺序按原文。"""
    _check_docx_archive(path)
    try:
        document = Document(str(path))
        blocks: list[dict] = []
        for item in _iter_docx_items(document):
            block = (
                _docx_paragraph_block(item)
                if isinstance(item, Paragraph)
                else _docx_table_block(item)
            )
            if block is not None:
                blocks.append(block)
        return blocks
    except Exception as exc:
        raise ValueError("DOCX 无法解析：文件损坏或已加密") from exc


def extract_pdf_text(path: Path) -> list[str]:
    """pypdf 逐页抽文本（最多前 MAX_PDF_TEXT_PAGES 页）；打不开就返回空列表。"""
    try:
        reader = PdfReader(str(path))
        return [
            page.extract_text() or ""
            for page in reader.pages[:MAX_PDF_TEXT_PAGES]
        ]
    except Exception:  # 加密 / 结构损坏：统一当「文本层不可用」
        return []


def render_pdf_pages(path: Path, limit: int, out_dir: Path) -> list[Path]:
    """把 PDF 前 limit 页渲染成 180 DPI PNG，返回落盘路径（调用方负责清理目录）。

    加密 / 损坏统一转成可读 ValueError：上层把这个消息写进 error 事件，
    不能让用户看到 pymupdf 的英文栈。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise ValueError("PDF 无法打开：文件损坏或已加密") from exc
    try:
        if document.needs_pass:
            raise ValueError("PDF 已加密，请先解除密码后再上传")
        for index in range(min(limit, document.page_count)):
            pixmap = document.load_page(index).get_pixmap(dpi=RENDER_DPI)
            image_path = out_dir / f"page-{index + 1}.png"
            pixmap.save(str(image_path))
            images.append(image_path)
    except ValueError as exc:
        # 自己抛的加密提示原样透出；pymupdf 自己的 ValueError 统一换成人话
        if "加密" in str(exc):
            raise
        raise ValueError("PDF 渲染失败：文件损坏或已加密") from exc
    except Exception as exc:
        raise ValueError("PDF 渲染失败：文件损坏或已加密") from exc
    finally:
        document.close()
    return images


def _mock_note(note: str, source: str) -> str:
    """mock 下补一句「用了哪套样例集」；真实 provider 原样返回。"""
    if config.VLM_PROVIDER != "mock":
        return note
    return f"{note}；{fixtures.match_note(source)}"


def _assert_pdf_readable(path: Path) -> None:
    """在 mock 分支之前先确认文件真是可读 PDF，避免加密/损坏文件被伪造卡片。"""
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise ValueError("PDF 无法打开：文件损坏或不是 PDF") from exc
    try:
        if document.needs_pass:
            raise ValueError("PDF 已加密，请先解除密码后再上传")
        if document.page_count <= 0:
            raise ValueError("PDF 没有可读页面")
    finally:
        document.close()


async def _pdf_document(path: Path, source: str) -> tuple[list[dict], str]:
    """PDF：文本层可用就直读，否则渲染前 N 页交给视觉模型。"""
    _assert_pdf_readable(path)
    pages = extract_pdf_text(path)
    if is_text_usable(pages):
        blocks = truncate_blocks(text_blocks("\n\n".join(pages)))
        note = _mock_note(f"文本层可用，直接解析（{len(pages)} 页）", source)
        return blocks, note

    if config.VLM_PROVIDER == "mock":
        # mock 视觉模型不看像素：渲染只会把同一套样例重复 N 遍，改按文件名选套
        blocks = truncate_blocks(fixtures.blocks_for(source))
        note = _mock_note(
            f"文本层不可用（乱码或过短），文档共 {len(pages)} 页；mock 模式不渲染",
            source,
        )
        return blocks, note

    with tempfile.TemporaryDirectory(prefix=RENDER_TMP_PREFIX) as tmp_dir:
        images = render_pdf_pages(path, config.MAX_DOC_PAGES, Path(tmp_dir))
        blocks: list[dict] = []
        for image_path in images:
            blocks.extend(await vlm.recognize(image_path))

    note = (
        f"文本层不可用（乱码或过短），文档共 {len(pages)} 页，"
        f"取前 {len(images)} 页渲染成图解析"
    )
    return truncate_blocks(blocks), note


async def read_document(
    path: Path, source_name: str | None = None
) -> tuple[list[dict], str]:
    """按后缀解析文档，返回 (版面块, 给事件日志的一句说明)。

    `source_name` 是上传时的原始文件名：mock 靠它选样例集（落盘名是 uuid）。
    """
    path = Path(path)
    source = source_name or path.name
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return await _pdf_document(path, source)
    if suffix == ".docx":
        blocks = truncate_blocks(docx_blocks(path))
        return blocks, _mock_note(f"docx 直接解析：{len(blocks)} 个段落/表格块", source)
    if suffix in {".txt", ".md"}:
        blocks = truncate_blocks(text_blocks(_read_text_file(path), mark_headings=True))
        note = _mock_note(f"文本直接解析：{len(blocks)} 段（空行分段，按 # 认标题）", source)
        return blocks, note
    raise ValueError(f"不支持的文档类型：{suffix}")


async def read_blocks(path: Path, source_name: str | None = None) -> list[dict]:
    """只要版面块时的入口（结构与本项目的视觉识别输出一致）。"""
    blocks, _ = await read_document(path, source_name=source_name)
    return blocks
