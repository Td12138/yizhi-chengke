"""视觉模型调用层：图像 → 结构化版面块；mock 走固定样例。"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from .. import config
from . import fixtures

REQUEST_TIMEOUT = 120.0
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

VLM_SYSTEM_PROMPT = (
    "你是版面识别器。把图片内容转成结构化块，输出 JSON："
    '{"blocks": [{"type": "title"|"text"|"formula"|"table", '
    '"content": str, "latex": str}]}。'
    "规则：数学公式放 formula 块并给出 latex；表格压成单行文字放 table 块；"
    "看不清的内容写 [无法识别]，不要猜。只输出 JSON。"
)


async def recognize(image_path: Path) -> list[dict[str, Any]]:
    if config.VLM_PROVIDER == "mock":
        return [dict(block) for block in fixtures.MOCK_BLOCKS]
    return await _remote(image_path)


async def _remote(image_path: Path) -> list[dict[str, Any]]:
    if not config.VLM_BASE_URL or not config.VLM_API_KEY:
        raise RuntimeError("VLM_BASE_URL / VLM_API_KEY 未配置，无法使用非 mock 提供方")

    mime = MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/png")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    messages = [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别这张图片。"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        },
    ]
    body = {
        "model": config.VLM_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    url = config.VLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.VLM_API_KEY}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return _loads(content).get("blocks", [])


def _loads(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"模型输出不是 JSON：{text[:200]}")
        return json.loads(match.group(0))
