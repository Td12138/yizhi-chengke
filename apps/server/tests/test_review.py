"""AI 批改：结构固定、分数按规则算、评语真分片（拼接等于最终文本）。"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.agents.review import render_review_text, split_chunks
from app.events import EventBus
from app.main import app
from app.orchestrator import Orchestrator
from app.providers import fixtures, llm

RUN_ID = "test-review"
MISSING_ID = 99999
# 8–20 字切片：这就是「前端能逐字看到」的最小保证
CHUNK_MIN, CHUNK_MAX = 8, 20
RESULT_KEYS = {
    "score",
    "verdict",
    "points",
    "issues",
    "suggestion",
    "reference",
    "steps",
    "first_error",
    "better_method",
    "method_comparison",
}

AXIS_Q = {
    "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的对称轴。",
    "qtype": "fill",
    "answer": r"$x = 2$",
}
VERTEX_Q = {
    "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的顶点坐标。",
    "qtype": "fill",
    "answer": r"$(2,\ -1)$",
}
SQUARE_Q = {
    "stem_latex": r"把 $y = x^{2} - 4x + 3$ 化为顶点式。",
    "qtype": "fill",
    "answer": r"$y = (x-2)^{2} - 1$",
}
VALUE_Q = {
    "stem_latex": r"若 $y = x^{2} - 4x + 3$ 在 $x = 2$ 处的函数值为 $m$，则 $m$ 等于？",
    "qtype": "fill",
    "answer": r"$-1$",
}
CHOICE_Q = {
    "stem_latex": r"$y = x^{2} - 4x + 3$ 与 $x$ 轴的交点个数是？",
    "qtype": "choice",
    "options": ["0 个", "1 个", "2 个", "无法确定"],
    "answer": "C",
}


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


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
    assert len(done) == 1, "批改必须只有一条 orchestrator/done"
    return done[0].payload["result"]


def _chunks(events: list) -> list[str]:
    return [
        e.payload["chunk"]
        for e in events
        if e.agent == "review" and e.phase == "delta" and "chunk" in e.payload
    ]


# ---- 接口 ----


def test_review_returns_run_id(client):
    question_id = _seed_question(AXIS_Q)
    response = client.post(
        "/api/review", json={"question_id": question_id, "user_answer": "$x = 2$"}
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert isinstance(run_id, str) and len(run_id) == 12


def test_review_unknown_question_returns_404(client):
    response = client.post(
        "/api/review", json={"question_id": MISSING_ID, "user_answer": "x = 2"}
    )
    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["detail"]


def test_sse_channel_carries_review_events(client):
    """走真实 HTTP：POST 拿 run_id，再按现有 SSE 通道把事件读完。"""
    question_id = _seed_question(AXIS_Q)
    run_id = client.post(
        "/api/review", json={"question_id": question_id, "user_answer": "2"}
    ).json()["run_id"]

    with client.stream("GET", f"/api/events/{run_id}") as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())

    events = [
        json.loads(line[len("data: ") :])
        for line in raw.splitlines()
        if line.startswith("data: ") and line != "data: {}"
    ]
    assert [e["agent"] for e in events][0] == "review"
    assert events[0]["phase"] in {"queued", "running"}
    assert any(e["agent"] == "review" and e["phase"] == "delta" for e in events)
    last = events[-1]
    assert (last["agent"], last["phase"]) == ("orchestrator", "done")
    assert last["payload"]["result"]["verdict"] == "correct"


# ---- 结构与事件顺序 ----


def test_result_has_full_structure():
    result = _result(_review_events(AXIS_Q, r"$x = 2$"))

    assert RESULT_KEYS < set(result)
    assert isinstance(result["score"], int) and 0 <= result["score"] <= 100
    assert result["verdict"] in {"correct", "partial", "wrong"}
    assert result["points"] and all(
        set(point) == {"hit", "text"} and isinstance(point["hit"], bool)
        for point in result["points"]
    )
    assert isinstance(result["issues"], list)
    assert isinstance(result["suggestion"], str) and result["suggestion"]
    assert result["reference"] == AXIS_Q["answer"]


def test_event_order_is_running_delta_done():
    events = _review_events(AXIS_Q, r"$x = 2$")
    phases = [(e.agent, e.phase) for e in events]

    assert phases[0][0] == "review"
    assert phases[0][1] in {"queued", "running"}
    assert ("review", "done") in phases
    assert phases[-1] == ("orchestrator", "done")
    # 所有 chunk 都在 review/done 之前，done 之后不再补字
    done_index = phases.index(("review", "done"))
    chunk_indexes = [
        index
        for index, event in enumerate(events)
        if event.agent == "review" and "chunk" in event.payload
    ]
    assert chunk_indexes and max(chunk_indexes) < done_index


def test_attempt_is_recorded():
    question_id = _seed_question(AXIS_Q)
    bus = EventBus()
    bus.create(RUN_ID)
    asyncio.run(Orchestrator(bus).review(RUN_ID, question_id, "3"))

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT user_answer, is_correct, error_reason FROM attempt WHERE question_id = ?",
            (question_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["user_answer"] == "3"
    assert rows[0]["is_correct"] == 0
    assert rows[0]["error_reason"], "扣分原因要落库，错题回顾才有内容"


def test_exact_answer_gets_full_score():
    result = _result(_review_events(AXIS_Q, r"$ x = 2 $"))

    assert result["score"] == 100
    assert result["verdict"] == "correct"
    assert all(point["hit"] for point in result["points"])
    assert result["issues"] == []


def test_numeric_equivalent_gets_85():
    """只填等号右边的 2：与标准答案 x = 2 数值等价。"""
    result = _result(_review_events(AXIS_Q, "2"))

    assert result["score"] == 85
    assert result["verdict"] == "correct"


def test_partial_keywords_get_partial_band():
    result = _result(_review_events(AXIS_Q, "对称轴是 x = -b/(2a)，我算出来是 3"))

    assert 40 <= result["score"] <= 70
    assert result["verdict"] == "partial"
    assert any(point["hit"] for point in result["points"])
    assert any(not point["hit"] for point in result["points"])
    assert result["issues"]


def test_unrelated_answer_gets_wrong_band():
    result = _result(_review_events(AXIS_Q, "今天天气不错"))

    assert 10 <= result["score"] <= 30
    assert result["verdict"] == "wrong"
    assert not any(point["hit"] for point in result["points"])
    assert len(result["issues"]) >= 2


def test_empty_answer_is_wrong_and_says_so():
    result = _result(_review_events(AXIS_Q, "   "))

    assert result["verdict"] == "wrong"
    assert "没有作答" in result["issues"][0]


def test_choice_question_scores_by_letter():
    right = _result(_review_events(CHOICE_Q, "C"))
    wrong = _result(_review_events(CHOICE_Q, "B"))

    assert (right["score"], right["verdict"]) == (100, "correct")
    assert wrong["verdict"] in {"partial", "wrong"}
    assert wrong["score"] < right["score"]


# ---- 步骤诊断：错在哪一步 + 更优解法 ----


def test_result_has_step_diagnostics():
    result = _result(_review_events(AXIS_Q, "2"))
    steps = result["steps"]

    assert 2 <= len(steps) <= 5, steps
    assert [step["index"] for step in steps] == list(range(1, len(steps) + 1))
    assert all(set(step) == {"index", "expect", "found", "note"} for step in steps)
    assert all(step["expect"] and step["note"] for step in steps)

    first_error = result["first_error"]
    assert set(first_error) == {"step", "reason"}
    assert 1 <= first_error["step"] <= len(steps)
    missing = [step["index"] for step in steps if not step["found"]]
    assert first_error["step"] == (missing[0] if missing else 1)


def test_exact_answer_has_no_first_error():
    result = _result(_review_events(AXIS_Q, r"$x = 2$"))

    assert result["first_error"] is None
    assert all(step["found"] for step in result["steps"])


def test_wrong_value_points_at_the_broken_step():
    """公式写对、数值算错：错步必须落在第 2 步，reason 写出「该得到 X，你写的是 Y」。"""
    answer = "对称轴公式 x = -b/(2a)，代入算出 x = 3"
    result = _result(_review_events(AXIS_Q, answer))

    first_error = result["first_error"]
    assert first_error["step"] == 2
    assert "该得到 2" in first_error["reason"]
    assert "3" in first_error["reason"]
    assert result["steps"][0]["found"] is True
    assert result["steps"][1]["found"] is False


def test_unrelated_answer_fails_at_step_one():
    result = _result(_review_events(AXIS_Q, "今天天气不错"))

    assert result["first_error"]["step"] == 1
    assert result["verdict"] == "wrong"


def test_steps_come_from_solution_segments():
    question = {
        **AXIS_Q,
        "solution": "先求对称轴 x = 2；再把 x = 2 代回原式得 y = -1。最后写出顶点 (2, -1)。",
    }
    result = _result(_review_events(question, r"$(2, -1)$"))

    assert [step["expect"] for step in result["steps"]] == [
        "先求对称轴 x = 2",
        "再把 x = 2 代回原式得 y = -1",
        "最后写出顶点 (2, -1)",
    ]


def test_steps_are_capped_at_five():
    question = {**AXIS_Q, "solution": "；".join(f"第{i}步" for i in range(1, 8))}
    result = _result(_review_events(question, "3"))

    assert len(result["steps"]) == 5


def test_better_method_is_topic_specific():
    axis = _result(_review_events(AXIS_Q, "3"))
    intersection = _result(_review_events(CHOICE_Q, "B"))
    square = _result(_review_events(SQUARE_Q, "3"))

    assert axis["better_method"]["name"] == "顶点公式"
    assert "求导" in axis["better_method"]["why"]
    assert intersection["better_method"]["name"] == "判别式"
    assert square["better_method"]["name"] == "配方法"
    assert all(
        method["better_method"]["steps"]
        for method in (axis, intersection, square)
    )
    assert "推荐用顶点公式" in axis["method_comparison"]
    assert "2 步" in axis["method_comparison"]
    assert axis["method_comparison"] != intersection["method_comparison"]


def test_method_comparison_never_counts_student_steps():
    """mock 不知道学生写了几步，任何「你的解法 N 步」都是编造。"""
    for answer in ("-2", "3", "对称轴是 x = 3", "解：因为 x = -b/(2a)，所以 x = 2"):
        result = _result(_review_events(SQUARE_Q, answer))
        comparison = result["method_comparison"]
        assert "你的解法" not in comparison
        assert "你写了" not in comparison


def test_notes_only_quote_what_student_wrote():
    result = _result(_review_events(AXIS_Q, "-2"))
    notes = " ".join(step["note"] for step in result["steps"])

    assert "作答只有一个结果值 -2，未体现推导过程" in notes
    assert "作答里的数值是 -2" in notes
    assert "省略" not in notes
    assert "你的" not in notes


def test_step_target_keeps_sign_out_of_formulas():
    """回归：归一化会吞掉括号，`y=(x-2)²-1` 的末位 -1 不能被读成「该得到 1」。"""
    assert fixtures._step_target("把 a、b 代入算出 $x = 2$") == 2
    assert fixtures._step_target("算出结果 $-1$") == -1
    assert fixtures._step_target(r"写成顶点式 $y = (x-2)^{2} - 1$") is None
    assert fixtures._step_target("写出对称轴公式 x = -b/(2a)") is None


def test_vertex_form_notes_do_not_invent_a_target():
    result = _result(_review_events(SQUARE_Q, "-2"))

    assert [step["note"] for step in result["steps"]] == [
        "缺失：作答只有一个结果值 -2，未体现推导过程",
        "缺失：作答只有一个结果值 -2，未体现推导过程",
    ]


def test_point_issues_state_only_observable_facts():
    result = _result(_review_events(SQUARE_Q, "-2"))
    issues = " ".join(result["issues"])

    assert "配方" in issues or "顶点式" in issues
    # 不能声称学生「用了配方法」之类的推断
    assert "你用了" not in issues
    assert "你的解法" not in issues


def test_method_comparison_affirms_recommended_method():
    result = _result(_review_events(AXIS_Q, "对称轴直接用顶点公式 x = -b/(2a)"))

    assert "你用的就是推荐解法" in result["method_comparison"]
    assert "顶点公式" in result["method_comparison"]


def test_narrative_leads_with_error_then_better_method():
    text = _result(_review_events(AXIS_Q, "3"))["text"]

    assert text.index("错在第") < text.index("为什么") < text.index("更优解法")
    assert "顶点公式" in text


# ---- 文案按题型区分（不是一套模板套到底）----


def test_point_text_depends_on_question_type():
    axis = _result(_review_events(AXIS_Q, "3"))
    vertex = _result(_review_events(VERTEX_Q, "3"))
    square = _result(_review_events(SQUARE_Q, "3"))

    axis_texts = [point["text"] for point in axis["points"]]
    vertex_texts = [point["text"] for point in vertex["points"]]
    square_texts = [point["text"] for point in square["points"]]

    assert axis_texts != vertex_texts
    assert vertex_texts != square_texts
    assert any("对称轴" in text for text in axis_texts)
    assert any("坐标" in text for text in vertex_texts)
    assert any("配方" in text or "完全平方" in text for text in square_texts)


def test_issues_and_suggestion_depend_on_question_type():
    axis = _result(_review_events(AXIS_Q, "3"))
    vertex = _result(_review_events(VERTEX_Q, "3"))
    value = _result(_review_events(VALUE_Q, "3"))

    assert axis["suggestion"] != vertex["suggestion"]
    assert axis["issues"] != vertex["issues"]
    assert axis["suggestion"] != value["suggestion"]


# ---- 流式：分片数量与拼接一致性 ----


def test_deltas_are_chunked_and_concatenate_to_result_text():
    events = _review_events(AXIS_Q, "2")
    chunks = _chunks(events)
    result = _result(events)

    assert len(chunks) >= 3, "必须分多条 delta，一次性给结果不算流式"
    assert all(CHUNK_MIN <= len(chunk) <= CHUNK_MAX for chunk in chunks), chunks
    assert "".join(chunks) == result["text"]
    # 拼接结果必须等于「按结构重心渲染一遍」的完整评语：防止切片时丢字/重复
    assert "".join(chunks) == render_review_text(result)
    assert str(result["score"]) in result["text"]
    assert AXIS_Q["answer"] in result["text"]


def test_split_chunks_merges_short_tail_without_losing_chars():
    text = "一二三四五六七八九十" * 2 + "末"  # 25 字：12 + 12 + 1，末片需并入前一片
    chunks = split_chunks(text)

    assert "".join(chunks) == text
    assert all(CHUNK_MIN <= len(chunk) <= CHUNK_MAX for chunk in chunks), chunks
    assert split_chunks("短短") == ["短短"]  # 文本本身不足下限：一整片，不硬凑


def test_chunks_cover_whole_text_without_loss_or_overlap():
    events = _review_events(VERTEX_Q, r"$(2, -1)$")
    chunks = _chunks(events)
    text = _result(events)["text"]

    assert "".join(chunks) == text
    # 每个片段的起点在原文里严格递增：不重复、不乱序
    starts = []
    cursor = 0
    for chunk in chunks:
        assert text.startswith(chunk, cursor)
        starts.append(cursor)
        cursor += len(chunk)
    assert starts == sorted(starts)
    assert cursor == len(text)


def test_midstream_failure_discards_partial_text(monkeypatch):
    """流式中途断掉：回退评语不能把中断前的半句拼进最终文本。"""
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    stale = "中断前已经发出的半句绝不能混进最终文本里"

    async def broken_stream(*_args, **_kwargs):
        yield stale
        raise RuntimeError("stream broken")

    async def fake_json(*_args, **_kwargs):
        return {
            "score": 40,
            "verdict": "partial",
            "issues": ["结果不对"],
            "suggestion": "把数字代进去",
        }

    monkeypatch.setattr(llm, "chat_stream", broken_stream)
    monkeypatch.setattr(llm, "chat_json", fake_json)

    result = _result(_review_events(AXIS_Q, "3"))

    assert "中断前已经发出的半句" not in result["text"]
    assert result["text"] == render_review_text(result)
    assert result["score"] == 40
