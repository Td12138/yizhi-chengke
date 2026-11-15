"""原图归档：上传的临时 uuid 文件名 → 按识别标题重命名，纯函数、不碰数据库。

调用顺序固定：识别 + 萃取拿到标题 → `rename_after_ocr` → 再回写 source 表。
标题拿不到（识别失败）时原样返回，临时名保留，绝不删文件。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

# 文件名禁用字符：Windows 保留字符 + 路径分隔符 + 控制字符
INVALID_CHARS = re.compile(r'[\x00-\x1f\\/:*?"<>|]')
# 连续空白折成一个连字符
WHITESPACE = re.compile(r"\s+")
# 删字符后可能留下连续连字符，压回一个
DASH_RUN = re.compile(r"-{2,}")
# 标题最多保留多少字符（不含日期前缀与扩展名）
MAX_NAME_CHARS = 40
# 重名时追加的序号起点
FIRST_SUFFIX = 2
# 连续撞名最多尝试多少次：正常目录远到不了，防止异常目录把线程卡死
MAX_NAME_ATTEMPTS = 1000
# 日期前缀格式
DATE_FORMAT = "%Y-%m-%d"


def slugify(title: str, max_chars: int = MAX_NAME_CHARS) -> str:
    """标题 → 安全文件名主体：去非法字符、空白折成 -、超长截断。

    清洗后为空（标题只有非法字符）时返回空串，由调用方决定退回原名。
    """
    text = WHITESPACE.sub("-", str(title or "").strip())
    text = INVALID_CHARS.sub("", text)
    text = DASH_RUN.sub("-", text)
    # Windows 不接受结尾的 - 和 .，截断后再收一次
    text = text.strip("-.")
    if len(text) > max_chars:
        text = text[:max_chars].strip("-.")
    return text


def unique_path(path: Path) -> Path:
    """目标已存在时追加 -2、-3 …，返回一个确定不存在的路径。"""
    path = Path(path)
    if not path.exists():
        return path
    index = FIRST_SUFFIX
    while True:
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def dated_stem(title: str, when: float | None = None) -> str:
    """归档文件名主体：<YYYY-MM-DD>-<标题>；标题清洗后为空则返回空串。"""
    slug = slugify(title)
    if not slug:
        return ""
    moment = time.time() if when is None else when
    stamp = time.strftime(DATE_FORMAT, time.localtime(moment))
    return f"{stamp}-{slug}"


def _reserve_target(path: Path) -> Path:
    """原子抢占目标名：O_CREAT|O_EXCL 建空占位文件，撞名就试 -2、-3 …

    先 `exists()` 再改名是 TOCTOU：两个线程同时看到「不存在」，会互相覆盖。
    这里让内核保证只有一个线程能创建同名文件，其余线程直接顺延。
    """
    index = 0
    while index < MAX_NAME_ATTEMPTS:
        candidate = (
            path
            if index == 0
            else path.with_name(
                f"{path.stem}-{FIRST_SUFFIX + index - 1}{path.suffix}"
            )
        )
        try:
            handle = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            index += 1
            continue
        os.close(handle)
        return candidate
    raise OSError(f"重名文件过多，无法为 {path.name} 找到可用文件名")


def rename_after_ocr(image_path: Path, title: str, when: float | None = None) -> Path:
    """把上传图按标题重命名到同目录，返回实际路径（标题为空时返回原路径）。

    只改文件名、不改扩展名；重名追加 -2、-3。改名失败抛 OSError 由调用方决定怎么兜底。
    """
    image_path = Path(image_path)
    stem = dated_stem(title, when)
    if not stem or not image_path.is_file():
        return image_path
    target = image_path.with_name(f"{stem}{image_path.suffix}")
    if target == image_path:
        return image_path
    target = _reserve_target(target)
    try:
        image_path.replace(target)
    except OSError:
        # 占位文件是我们刚建的：改名失败就撤掉，免得留下 0 字节假归档
        target.unlink(missing_ok=True)
        raise
    return target


def pick_title(blocks: list[dict], cards: list[dict]) -> str:
    """归档标题：优先识别出的 title 版面块，退而用第一张卡片标题，都没有返回空串。"""
    for block in blocks or []:
        if block.get("type") == "title" and str(block.get("content", "")).strip():
            return str(block["content"]).strip()
    for card in cards or []:
        title = str(card.get("title", "")).strip()
        if title:
            return title
    return ""
