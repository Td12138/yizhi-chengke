"""LLM 调用层：OpenAI 兼容协议，Provider 可切换；mock 走固定教学样例。"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import httpx

from .. import config
from ..db import search_cards
from . import fixtures

# 单次请求超时（秒）
REQUEST_TIMEOUT = 90.0
# 流式响应里每行的前缀（OpenAI 兼容 SSE）
SSE_DATA_PREFIX = "data:"
# 流结束标记
SSE_DONE = "[DONE]"

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
    # 批改走流式：先流中文评语（用户能边等边看），末尾给一段 JSON 供结构化解析
    "review": (
        "你是批改助手。给定题目、标准答案与学生作答："
        "先写 3-6 句中文评语，顺序固定——先指出错在第几步，再说明为什么错，最后给更优解法建议；"
        "评语后面另起一行给出 fenced JSON："
        '```json\n{"score": 0-100 整数, "verdict": "correct"|"partial"|"wrong", '
        '"points": [{"hit": bool, "text": str}], '
        '"steps": [{"index": int, "expect": "该步应该做什么", "found": bool, '
        '"note": "学生的实际写法或缺失说明"}], '
        '"first_error": {"step": int, "reason": str} 或 null, '
        '"better_method": {"name": str, "why": str, "steps": [str]}, '
        '"method_comparison": str, '
        '"issues": [str], "suggestion": str}\n```。'
        "steps 按标准答案的解题过程拆成 2-5 步；first_error 是第一个未覆盖或数值不符的步骤，"
        "全部都覆盖时给 null；better_method 只在学生解法不是最优时给，并说明为什么更优。"
        "评语必须与 JSON 结论一致；不要解释格式本身。"
    ),
}

# 批改任务的 JSON schema 提示：chat_json 回退时告诉模型只回 JSON
REVIEW_JSON_HINT = (
    "只输出 JSON："
    '{"score": 0-100 整数, "verdict": "correct"|"partial"|"wrong", '
    '"points": [{"hit": bool, "text": str}], '
    '"steps": [{"index": int, "expect": str, "found": bool, "note": str}], '
    '"first_error": {"step": int, "reason": str} 或 null, '
    '"better_method": {"name": str, "why": str, "steps": [str]}, '
    '"method_comparison": str, "issues": [str], "suggestion": str}'
)


def _http_client() -> httpx.AsyncClient:
    """HTTP 客户端工厂：测试用 monkeypatch 注入 httpx.MockTransport，不走真实网络。"""
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT)


async def chat_json(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if config.LLM_PROVIDER == "mock":
        return _mock(task, payload)
    return await _remote(task, payload)


async def chat_stream(task: str, payload: dict[str, Any]) -> AsyncIterator[str]:
    """流式对话：边收边 yield 文本增量（OpenAI 兼容 SSE）。

    协议假设：POST {LLM_BASE_URL}/chat/completions，body 带 ``stream: true``；
    响应是 ``data: {...}`` 行，增量在 ``choices[0].delta.content``，以 ``data: [DONE]`` 结束。
    这是 OpenAI 兼容格式（qwen / glm / deepseek 同构），不是各家的私有协议。

    mock 没有真实 token 流：这里一次性 yield 整段 JSON，切片节奏由调用方负责
    （`ReviewAgent` 用固定 sleep 模拟逐字，注释已标明是模拟）。
    """
    if config.LLM_PROVIDER == "mock":
        yield json.dumps(_mock(task, payload), ensure_ascii=False)
        return
    async for piece in _stream_remote(task, payload):
        yield piece


def _mock(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "extract":
        # source 是上传时的原始文件名（不是 uuid 临时名），mock 靠它选样例集
        return {"cards": fixtures.cards_for(payload.get("source"))}
    if task == "compose":
        return {"questions": fixtures.mock_questions(int(payload.get("round", 1)))}
    if task == "explain":
        return fixtures.mock_explain(
            payload.get("question") or {}, str(payload.get("user_answer", ""))
        )
    if task == "review":
        return fixtures.mock_review(
            payload.get("question") or {}, str(payload.get("user_answer", ""))
        )
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


def _check_ready() -> None:
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


def _request_parts(
    task: str, payload: dict[str, Any], *, json_mode: bool
) -> tuple[str, dict, dict]:
    """拼出 (url, body, headers)；流式与非流式共用一个来源，避免两套参数漂移。

    json_mode=False 时不发 response_format：批改要先流自由文本评语，
    而 json_object 模式会禁止模型输出评语（OpenAI 兼容语义）。
    """
    _check_ready()
    prompt = SYSTEM_PROMPTS[task]
    if task == "review":
        prompt = f"{prompt}\n\n{REVIEW_JSON_HINT}"
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    return url, body, headers


async def _stream_remote(task: str, payload: dict[str, Any]) -> AsyncIterator[str]:
    """真实流式：逐行解析 SSE，收到 [DONE] 立即停，不缓冲整段。"""
    url, body, headers = _request_parts(task, payload, json_mode=task != "review")
    body["stream"] = True
    async with _http_client() as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith(SSE_DATA_PREFIX):
                    continue  # 空行 / 注释 / event: 之类的心跳
                data = line[len(SSE_DATA_PREFIX) :].strip()
                if data == SSE_DONE:
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # 单行坏数据不该中断整个流
                choices = chunk.get("choices") or [{}]
                delta = (choices[0] or {}).get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content


async def _remote(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    url, body, headers = _request_parts(task, payload, json_mode=True)
    async with _http_client() as client:
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
