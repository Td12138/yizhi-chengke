"""LLM 调用层：OpenAI 兼容协议，Provider 可切换；mock 走固定教学样例。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .. import config
from ..db import search_cards
from . import fixtures

# 单次请求超时（秒）
REQUEST_TIMEOUT = 90.0

SYSTEM_PROMPTS = {
    "extract": (
        "你是知识点萃取器。给定板书/讲义的结构化版面块，输出 JSON："
        '{"cards": [{"title": str, "summary": str, "subject": str, '
        '"tags": [str], "formula": str, "body": str}]}。'
        "要求：每张卡片只讲一个知识点，formula 用 LaTeX，body 保留原文细节。只输出 JSON。"
    ),
    "compose": (
        "你是命题器。根据知识点卡片和用户要求出题，输出 JSON："
        '{"questions": [{"stem_latex": str, "qtype": "fill"|"choice", '
        '"options": [str], "answer": str, "solution": str, '
        '"difficulty": "easy"|"medium"|"hard", '
        '"verify": {"expr": "sympy 可解析的表达式", "subs": {"x": 数值}, "expect": 数值}}]}。'
        "要求：题干数学内容用 LaTeX（$...$）；verify 必须给出可用代入数值的校验式；只输出 JSON。"
    ),
    "explain": (
        "你是讲解器。针对用户答错的题，输出 JSON："
        '{"template": "function_slider", "title": str, '
        '"params": {"a": 数值, "b": 数值, "c": 数值, "x_min": 数值, "x_max": 数值}, '
        '"script": str}。'
        "script 用中文，先指出错因再给出可操作的理解路径，不超过 120 字。只输出 JSON。"
    ),
    "answer": (
        "你是答疑助手。仅依据给定卡片作答，输出 JSON："
        '{"answer": str, "citations": [卡片 id]}。'
        "卡片里没有的内容必须说明「材料中未涉及」，不得编造。只输出 JSON。"
    ),
}


async def chat_json(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if config.LLM_PROVIDER == "mock":
        return _mock(task, payload)
    return await _remote(task, payload)


def _mock(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "extract":
        return {"cards": fixtures.MOCK_CARDS}
    if task == "compose":
        return {"questions": fixtures.mock_questions(int(payload.get("round", 1)))}
    if task == "explain":
        return dict(fixtures.MOCK_EXPLAIN)
    if task == "answer":
        query = str(payload.get("query", ""))
        cards = search_cards(query, config.RAG_TOP_K)
        if not cards:
            return {"answer": "材料中未涉及该问题。", "citations": []}
        head = cards[0]
        body = head.get("body") or head.get("summary") or ""
        return {
            "answer": f"{head.get('title')}：{body}",
            "citations": [c["id"] for c in cards],
        }
    raise ValueError(f"未知任务：{task}")


async def _remote(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", config.LLM_BASE_URL),
            ("LLM_API_KEY", config.LLM_API_KEY),
            ("LLM_MODEL", config.LLM_MODEL),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"{' / '.join(missing)} 未配置，无法使用 {config.LLM_PROVIDER} 提供方"
            "（qwen / glm / deepseek 会自动补地址与文本模型，通常只需给 LLM_API_KEY）"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[task]},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    body = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return _loads(content)


def _loads(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"模型输出不是 JSON：{text[:200]}")
        return json.loads(match.group(0))
