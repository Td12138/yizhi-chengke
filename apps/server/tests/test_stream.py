"""流式链路：SSE 逐行解析（真流式）、失败可降级、出题/萃取逐条 delta。

所有 HTTP 都用 httpx.MockTransport 伪造，**不打真实网络**；
`LLM_API_KEY` 是测试假值，不来自任何真实配置。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import config, db, events
from app.events import EventBus
from app.orchestrator import Orchestrator
from app.providers import fixtures, llm

BASE_URL = "https://llm.example.com/v1"
API_KEY = "test-key-not-real"
MODEL = "test-model"
COUNT = 5
RUN_ID = "test-stream"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake"
# 批改切片边界（与 ReviewAgent 一致）
CHUNK_MIN, CHUNK_MAX = 8, 20

AXIS_Q = {
    "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的对称轴。",
    "qtype": "fill",
    "answer": r"$x = 2$",
}
DONE_LINE = b"data: [DONE]\n\n"
# 模型自己在 JSON 里写了一份标准答案：必须被题目表里的答案覆盖
MODEL_WRONG_REFERENCE = "模型编的标准答案"


def _sse_event(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


async def _collect(agen) -> list[str]:
    return [piece async for piece in agen]


def _json_reply(payload: dict) -> httpx.Response:
    content = json.dumps(payload, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _use_remote_provider(monkeypatch, handler) -> None:
    """把 provider 指到真实分支，但把 HTTP 换成 MockTransport。"""
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_BASE_URL", BASE_URL)
    monkeypatch.setattr(config, "LLM_API_KEY", API_KEY)
    monkeypatch.setattr(config, "LLM_MODEL", MODEL)
    monkeypatch.setattr(
        llm,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _seed_question(question: dict) -> int:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    return db.insert_questions(
        [{**question, "verified": True, "card_ids": []}]
    )[0]


def _review_events(question: dict, user_answer: str) -> list:
    question_id = _seed_question(question)
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).review(RUN_ID, question_id, user_answer))
    run = bus.get(RUN_ID)
    assert run is not None and run.closed is True
    return run.history


def _result(events: list) -> dict:
    done = [e for e in events if e.agent == "orchestrator" and e.phase == "done"]
    assert len(done) == 1
    return done[0].payload["result"]


def _chunks(events: list) -> list[str]:
    return [
        e.payload["chunk"]
        for e in events
        if e.agent == "review" and e.phase == "delta" and "chunk" in e.payload
    ]


class _Trickle(httpx.AsyncByteStream):
    """按片吐字节的响应体：记录每次被拉取的片号，用来证明「没有先读完整段」。"""

    def __init__(self, chunks: list[bytes], log: list[int]) -> None:
        self._chunks = chunks
        self._log = log

    async def __aiter__(self):
        for index, chunk in enumerate(self._chunks):
            self._log.append(index)
            yield chunk


# ---- chat_stream：SSE 解析 ----


def test_chat_stream_parses_sse_deltas_in_order(monkeypatch):
    body = _sse_event("对称轴") + _sse_event("是 x = 2") + DONE_LINE
    _use_remote_provider(monkeypatch, lambda request: httpx.Response(200, content=body))

    pieces = asyncio.run(_collect(llm.chat_stream("review", {"question": "q"})))

    assert pieces == ["对称轴", "是 x = 2"]


def test_chat_stream_stops_at_done_marker(monkeypatch):
    body = (
        _sse_event("第一段")
        + _sse_event("第二段")
        + DONE_LINE
        + _sse_event("DONE 之后的脏数据")
    )
    _use_remote_provider(monkeypatch, lambda request: httpx.Response(200, content=body))

    text = "".join(asyncio.run(_collect(llm.chat_stream("review", {}))))

    assert text == "第一段第二段"
    assert "脏数据" not in text


def test_chat_stream_skips_malformed_lines(monkeypatch):
    body = (
        b": keepalive\n\n"
        + b"data: not-json\n\n"
        + _sse_event("正文")
        + b'data: {"choices": [{"delta": {}}]}\n\n'
        + DONE_LINE
    )
    _use_remote_provider(monkeypatch, lambda request: httpx.Response(200, content=body))

    assert asyncio.run(_collect(llm.chat_stream("review", {}))) == ["正文"]


def test_chat_stream_is_incremental_not_buffered(monkeypatch):
    """首片必须在响应体读完之前就 yield：否则用户等待时间根本没缩短。"""
    log: list[int] = []
    chunks = [_sse_event("第一段"), _sse_event("第二段"), DONE_LINE]
    handler = lambda request: httpx.Response(200, stream=_Trickle(chunks, log))
    _use_remote_provider(monkeypatch, handler)

    async def scenario():
        agen = llm.chat_stream("review", {})
        first = await agen.__anext__()
        read_at_first_yield = len(log)
        rest = [piece async for piece in agen]
        return first, read_at_first_yield, rest

    first, read_at_first_yield, rest = asyncio.run(scenario())

    assert first == "第一段"
    assert read_at_first_yield < len(chunks)
    assert rest == ["第二段"]


def test_stream_request_uses_openai_compatible_body(monkeypatch):
    bodies: list[dict] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        urls.append(str(request.url))
        return httpx.Response(200, content=DONE_LINE)

    _use_remote_provider(monkeypatch, handler)
    asyncio.run(_collect(llm.chat_stream("review", {"question": "q"})))

    assert urls == [f"{BASE_URL}/chat/completions"]
    assert bodies[0]["stream"] is True
    assert bodies[0]["model"] == MODEL
    # 批改要先流自由文本评语，json_object 模式会禁止评语输出
    assert "response_format" not in bodies[0]
    assert bodies[0]["messages"][0]["role"] == "system"


def test_mock_chat_stream_yields_whole_json(monkeypatch):
    """mock 没有 token 流：整段一次给出（切片节奏由 ReviewAgent 负责）。"""
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")

    pieces = asyncio.run(
        _collect(llm.chat_stream("review", {"question": AXIS_Q, "user_answer": "2"}))
    )

    assert len(pieces) == 1
    assert json.loads(pieces[0])["score"] == 85


# ---- ReviewAgent：真实流式与降级 ----


def _prose_and_fence(score: int = 92) -> str:
    payload = {
        "score": score,
        "verdict": "correct",
        "points": [{"hit": True, "text": "写出对称轴公式"}, {"hit": True, "text": "结果正确"}],
        "issues": ["没写出最终结论"],
        "suggestion": "把结论补上",
        "reference": MODEL_WRONG_REFERENCE,
    }
    return (
        "思路对了，公式也选对了，只是最后没把结论写出来，"
        "补一句 x = 2 就是满分答卷。\n"
        "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    )


def test_review_renders_structured_result_in_chunks(monkeypatch):
    """真实路径：先拿结构化 JSON，再按 8–20 字推出「人话版」评语。

    v0.1.7 之前是把模型原文直接流给界面，DeepSeek 返回 JSON 时用户会看到
    `{"score": 0, ...}` 一坨原文——现在改成先解析再渲染。
    """
    payload = {
        "score": 92,
        "verdict": "correct",
        "points": [
            {"hit": True, "text": "写出对称轴公式"},
            {"hit": True, "text": "结果正确"},
        ],
        "issues": ["没写出最终结论"],
        "suggestion": "把结论补上",
    }
    _use_remote_provider(monkeypatch, lambda request: _json_reply(payload))

    events = _review_events(AXIS_Q, "2")
    result = _result(events)
    chunks = _chunks(events)

    assert len(chunks) >= 3
    assert all(CHUNK_MIN <= len(chunk) <= CHUNK_MAX for chunk in chunks), chunks
    assert "".join(chunks) == result["text"]
    # 推给用户的是人话，不是 JSON
    assert not result["text"].lstrip().startswith("{")
    assert "得分" in result["text"] or "分" in result["text"]

    assert result["score"] == 92
    assert result["verdict"] == "correct"
    assert result["issues"] == ["没写出最终结论"]
    assert result["reference"] == AXIS_Q["answer"]
    # 模型没给步骤诊断：用本地确定性规则补齐，结构不缺项
    assert 2 <= len(result["steps"]) <= 5
    assert result["first_error"] is not None
    assert result["better_method"]["name"] == "顶点公式"


def test_model_step_diagnostics_override_local_rules(monkeypatch):
    """模型给了 steps / first_error / better_method 时以模型为准。"""
    payload = {
        "score": 70,
        "verdict": "partial",
        "points": [{"hit": True, "text": "用了顶点公式"}],
        "issues": ["最后一步漏了"],
        "suggestion": "补上结论",
        "steps": [
            {"index": 1, "expect": "模型给的步骤一", "found": True, "note": "写对了"},
            {"index": 2, "expect": "模型给的步骤二", "found": False, "note": "漏了"},
        ],
        "first_error": {"step": 2, "reason": "模型判定的错因"},
        "better_method": {"name": "换元法", "why": "少一步", "steps": ["换元", "回代"]},
        "method_comparison": "模型给的对比",
    }
    _use_remote_provider(monkeypatch, lambda request: _json_reply(payload))

    result = _result(_review_events(AXIS_Q, "x = 3"))

    assert [step["expect"] for step in result["steps"]] == [
        "模型给的步骤一",
        "模型给的步骤二",
    ]
    assert result["first_error"] == {"step": 2, "reason": "模型判定的错因"}
    assert result["better_method"]["name"] == "换元法"
    assert result["method_comparison"] == "模型给的对比"


def test_review_falls_back_when_model_json_fails(monkeypatch):
    """模型接口 500 → 回退本地规则，批改不能整个失败。"""
    _use_remote_provider(
        monkeypatch, lambda request: httpx.Response(500, json={"error": "upstream boom"})
    )

    events = _review_events(AXIS_Q, "x = 3")
    result = _result(events)

    notices = [
        e.payload.get("notice")
        for e in events
        if e.agent == "review" and e.phase == "delta" and "notice" in e.payload
    ]
    assert "json-fallback" in notices
    assert "".join(_chunks(events)) == result["text"]
    assert result["reference"] == AXIS_Q["answer"]


def test_review_parse_failure_falls_back_to_local_rules(monkeypatch):
    """模型胡写（不是 JSON）→ 结构化字段回退本地规则，评语照旧推出。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "这道题我不太确定，看起来还行。"}}]},
        )

    _use_remote_provider(monkeypatch, handler)

    events = _review_events(AXIS_Q, "2")
    result = _result(events)

    notices = [
        e.payload.get("notice")
        for e in events
        if e.agent == "review" and e.phase == "delta" and "notice" in e.payload
    ]
    assert "json-fallback" in notices
    assert result["score"] == 85  # 本地规则：与标准答案数值等价
    assert result["verdict"] == "correct"
    assert "".join(_chunks(events)) == result["text"]


# ---- 出题 / 萃取：逐条 delta ----


def _seed_cards() -> list[int]:
    source_id = db.insert_source("demo.png", fixtures.MOCK_BLOCKS)
    db.insert_cards(source_id, fixtures.MOCK_CARDS)
    return [card["id"] for card in db.list_cards()]


def test_compose_emits_one_delta_per_question():
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(
        Orchestrator(bus).generate(RUN_ID, _seed_cards(), "出几道基础题", COUNT)
    )
    events = bus.get(RUN_ID).history

    deltas = [e for e in events if e.agent == "compose" and e.phase == "delta"]
    # mock 下第 1 轮必被驳回 1 题，所以命题跑两轮
    assert len(deltas) == COUNT * 2
    assert all(isinstance(e.payload.get("question"), dict) for e in deltas)
    assert all(e.payload["question"].get("stem_latex") for e in deltas)
    assert [e.payload["index"] for e in deltas[:COUNT]] == list(range(1, COUNT + 1))
    assert all(e.payload["total"] == COUNT for e in deltas)
    assert len({e.payload["question"]["stem_latex"] for e in deltas[:COUNT]}) == COUNT

    done = [e for e in events if e.agent == "compose" and e.phase == "done"]
    assert len(done) == 2
    assert len(done[0].payload["questions"]) == COUNT


def test_extract_emits_one_delta_per_card():
    image = config.UPLOAD_DIR / "uuid.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(IMAGE_BYTES)

    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).ingest(RUN_ID, image))
    events = bus.get(RUN_ID).history

    deltas = [e for e in events if e.agent == "extract" and e.phase == "delta"]
    assert len(deltas) == len(fixtures.MOCK_CARDS)
    assert [e.payload["card"]["title"] for e in deltas] == [
        card["title"] for card in fixtures.MOCK_CARDS
    ]
    assert all(isinstance(e.payload["card"]["id"], int) for e in deltas)
    assert [e.payload["index"] for e in deltas] == list(
        range(1, len(fixtures.MOCK_CARDS) + 1)
    )


# ---- 事件上限：终止事件必须保留 ----

def test_terminal_event_survives_history_cap():
    run = events.Run("cap")
    for index in range(events.MAX_EVENTS_PER_RUN):
        run.append(events.Event("cap", "agent", "delta", f"第 {index} 条"))

    run.append(events.Event("cap", "orchestrator", "done", "交付完成"))

    assert len(run.history) == events.MAX_EVENTS_PER_RUN + 1
    assert run.history[-1].phase == "done"
    assert run.history[-1].message == "交付完成"


def test_non_terminal_event_is_dropped_at_cap():
    run = events.Run("cap")
    for index in range(events.MAX_EVENTS_PER_RUN):
        run.append(events.Event("cap", "agent", "delta", f"第 {index} 条"))

    run.append(events.Event("cap", "agent", "delta", "多出来的一条"))

    assert len(run.history) == events.MAX_EVENTS_PER_RUN
    assert run.history[-1].message != "多出来的一条"
