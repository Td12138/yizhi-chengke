"""批改 Agent：作答 → 得分点比对 → 流式评语（边算边发，不是算完再拆）。

两条路径，节奏不同、来源相同：

* mock provider：用 `fixtures.mock_review` 的确定性规则算分，评语由 `render_review_text`
  渲染成文本，再按 8–20 字切片 + `sleep(CHUNK_PACE_SECONDS)` 推送。
  **这是模拟流式**——没有真实 token 流，只是让前端看到逐字出现的效果。
  可信度边界：mock 只做字符串/数值比对，所以它**不判断学生用了什么方法**，
  只说明「哪一步的关键式/数值没出现」；`better_method` 是按题型固定推荐的，
  不是从作答里推断出来的。
* 真实 provider：`llm.chat_stream` 逐块返回模型输出，收到的增量立刻发 `delta`
  （攒够 8 字就发一片），用户等待时间因此真的缩短；模型在评语末尾附一段 fenced JSON
  供结构化解析。流式失败时回退到一次性 `chat_json`，并在事件里写明「已回退」。
  只有这条路径允许做方法论推断（模型确实读了作答），其结论原样透出。
  v0.1.5 起结构化结果含 `student_method`（方法归因）：从作答识别学生实际用的方法
  并判断是否成立——另一种正确解法会被承认，而不是因写法不同被扣分。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .. import config
from ..providers import fixtures, llm
from .base import Agent

# 评语切片下限/上限（字）：末片不足下限就并入前一片，保证每片都落在 8–20 字
CHUNK_MIN_CHARS = 8
CHUNK_MAX_CHARS = 20
# 常规切片长度：落在 [CHUNK_MIN_CHARS, CHUNK_MAX_CHARS] 内，且 12+7 <= 20 便于合并末片
CHUNK_SIZE = 12

VERDICT_LABELS = {"correct": "正确", "partial": "部分正确", "wrong": "错误"}
VERDICTS = tuple(VERDICT_LABELS)
SCORE_MIN = 0
SCORE_MAX = 100
# 分数 → 结论的兜底映射：模型只给分数没给 verdict 时用
CORRECT_MIN_SCORE = 85
PARTIAL_MIN_SCORE = 40

# 评语末尾的结构化 JSON：允许 ``` 围栏，也允许裸 JSON
JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def split_chunks(text: str) -> list[str]:
    """把文本切成 8–20 字的片段；末片太短就并进前一片，不丢字也不多切。"""
    pieces = [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    pieces = [piece for piece in pieces if piece]
    if len(pieces) > 1 and len(pieces[-1]) < CHUNK_MIN_CHARS:
        # 先 pop 再合并：写成 pieces[-2] += pieces.pop() 会因下标在 pop 前结算而互相覆盖
        tail = pieces.pop()
        pieces[-1] += tail
    return pieces


def render_review_text(review: dict) -> str:
    """把结构化批改结果渲染成人看的评语（mock 与回退路径共用同一套文案）。

    叙述顺序固定：错在第几步 → 为什么错 → 更优解法建议 → 得分点/建议/标准答案。
    """
    steps = review.get("steps") or []
    lines = [
        f"得分 {review['score']}/{SCORE_MAX} · {VERDICT_LABELS[review['verdict']]}",
    ]
    first_error = review.get("first_error")
    if first_error:
        expect = next(
            (
                step["expect"]
                for step in steps
                if step["index"] == first_error["step"]
            ),
            "",
        )
        # 结论正确但过程缺一步时不说「错」，避免自相矛盾
        head = "缺了" if review["verdict"] == "correct" else "错在"
        lines.append(f"{head}第 {first_error['step']} 步：{expect}")
        # 标题已经点明是哪一步，正文里不再重复「这一步应为…」
        reason = str(first_error["reason"])
        prefix = f"这一步应为「{expect}」"
        if prefix in reason:
            reason = reason.split(prefix, 1)[1].lstrip("，, ")
        lines.append(f"为什么：{reason}")
    elif steps:
        lines.append(f"{len(steps)} 步全部覆盖，没有错误步骤")

    student = review.get("student_method")
    if student:
        # 方法归因只在真实 provider 路径出现；另一种正确解法必须被承认
        label = "成立" if student["valid"] else "不成立"
        lines.append(f"你的解法：{student['name']}（{label}）")
        if student.get("note"):
            lines.append(f"  · {student['note']}")

    better = review.get("better_method") or {}
    if better.get("name") or better.get("steps"):
        lines.append(f"更优解法：{better.get('name', '')}")
        lines.extend(f"  · {step}" for step in better.get("steps") or [])
    if review.get("method_comparison"):
        # 对比文案里已经带了推荐理由，不再重复 why
        lines.append(str(review["method_comparison"]))

    if review.get("issues"):
        lines.append("问题：" + "；".join(str(issue) for issue in review["issues"]))
    if review.get("suggestion"):
        lines.append("建议：" + str(review["suggestion"]))
    if review.get("reference"):
        lines.append("标准答案：" + str(review["reference"]))
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """从流式文本里取结构化结果：先裸 JSON，再 ``` 围栏，最后贪婪截取 {...}。"""
    candidates = [text.strip()]
    fence = JSON_FENCE.search(text)
    if fence:
        candidates.append(fence.group(1))
    loose = JSON_OBJECT.search(text)
    if loose:
        candidates.append(loose.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _as_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return SCORE_MIN
    return max(SCORE_MIN, min(SCORE_MAX, score))


def _verdict_of(score: int) -> str:
    if score >= CORRECT_MIN_SCORE:
        return "correct"
    if score >= PARTIAL_MIN_SCORE:
        return "partial"
    return "wrong"


def _as_points(value: Any) -> list[dict]:
    points: list[dict] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        points.append({"hit": bool(item.get("hit")), "text": text})
    return points


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_steps(value: Any) -> list[dict]:
    """解题步骤：index 缺失按顺序补，没有 expect 的条目丢掉（无法判读）。"""
    steps: list[dict] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        expect = str(item.get("expect") or "").strip()
        if not expect:
            continue
        steps.append(
            {
                "index": _as_int(item.get("index"), len(steps) + 1),
                "expect": expect,
                "found": bool(item.get("found")),
                "note": str(item.get("note") or "").strip(),
            }
        )
    return steps


def _as_first_error(value: Any, steps: list[dict]) -> dict | None:
    """第一个错步：步骤号必须落在 steps 范围内，越界按「无此信息」处理。"""
    if not isinstance(value, dict):
        return None
    step = _as_int(value.get("step"), 0)
    reason = str(value.get("reason") or "").strip()
    if not steps or not 1 <= step <= len(steps) or not reason:
        return None
    return {"step": step, "reason": reason}


def _as_better_method(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    steps = [str(item).strip() for item in value.get("steps") or [] if str(item).strip()]
    if not name and not steps:
        return None
    return {"name": name, "why": str(value.get("why") or "").strip(), "steps": steps}


def _as_student_method(value: Any) -> dict | None:
    """方法归因（仅真实 provider 会给出）：没有方法名就视为没有归因。

    mock 规则不做语义推断，永远不产出该字段——这是 mock 与真实路径的能力边界。
    """
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "valid": bool(value.get("valid")),
        "note": str(value.get("note") or "").strip(),
    }


def _as_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def coerce_review(raw: Any, question: dict, base: dict | None = None) -> dict:
    """把模型/规则输出收敛成固定结构：分数夹到 0–100，结论只允许三态。

    `base` 是本地规则的确定性结果：模型少给的字段（steps / first_error /
    better_method 等）用它补齐，保证 /api/review 的返回结构永远完整。
    reference 一律取题目表里的标准答案，不采信模型自己写的那份。
    """
    source = raw if isinstance(raw, dict) else {}
    fallback = base if isinstance(base, dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        return source[key] if key in source else fallback.get(key, default)

    score = _as_score(pick("score"))
    verdict = str(pick("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        verdict = _verdict_of(score)
    steps = _as_steps(pick("steps"))
    return {
        "score": score,
        "verdict": verdict,
        "points": _as_points(pick("points")),
        "issues": _as_lines(pick("issues")),
        "suggestion": str(pick("suggestion") or "").strip(),
        "reference": str(question.get("answer") or ""),
        "steps": steps,
        "first_error": _as_first_error(pick("first_error"), steps),
        "student_method": _as_student_method(pick("student_method")),
        "better_method": _as_better_method(pick("better_method")),
        "method_comparison": str(pick("method_comparison") or "").strip(),
    }


class ReviewAgent(Agent):
    name = "review"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        # 已发出的全部 chunk 拼接：作为 result["text"]，保证「拼接 == 最终文本」
        self.streamed = ""

    async def _emit_chunk(self, chunk: str) -> None:
        self.streamed += chunk
        await self.emit("delta", "", chunk=chunk)

    async def _emit_all(self, text: str, pace: float) -> None:
        for chunk in split_chunks(text):
            await self._emit_chunk(chunk)
            await asyncio.sleep(pace)

    async def run(self) -> dict:
        question = self.ctx.state.get("question") or {}
        user_answer = str(self.ctx.state.get("user_answer", ""))
        payload = {"question": question, "user_answer": user_answer}
        await self.emit("running", "开始批改：逐条比对得分点")

        if config.LLM_PROVIDER == "mock":
            review = coerce_review(await llm.chat_json("review", payload), question)
            await self.emit(
                "delta",
                "规则批改完成，以下评语按 8–20 字切片模拟流式（mock 无真实 token 流）",
                notice="mock-simulated-stream",
            )
            await self._emit_all(render_review_text(review), config.CHUNK_PACE_SECONDS)
        else:
            review = await self._stream_review(payload, question, user_answer)

        review["text"] = self.streamed
        self.ctx.state["review"] = review
        await self.emit(
            "done",
            f"批改完成：{review['score']} 分 · {VERDICT_LABELS[review['verdict']]}",
        )
        return review

    async def _stream_review(
        self, payload: dict, question: dict, user_answer: str
    ) -> dict:
        """真实 provider：边收边发；流式不可用就整体回退，不让批改失败。

        `local` 是本地规则的确定性结果，只用于补模型没给的字段（steps /
        first_error / better_method）——步骤诊断本身是对 solution 与作答做
        关键词/数值比对，不依赖模型。
        """
        local = fixtures.mock_review(question, user_answer)
        buffer = ""
        try:
            async for piece in llm.chat_stream("review", payload):
                buffer += piece
                # 留够「一个标准片 + 一个下限片」再切：末片因此必然落在 8–19 字
                while len(buffer) >= CHUNK_MIN_CHARS + CHUNK_SIZE:
                    chunk, buffer = buffer[:CHUNK_SIZE], buffer[CHUNK_SIZE:]
                    await self._emit_chunk(chunk)
            if buffer:
                await self._emit_chunk(buffer)
        except Exception as exc:  # 网络抖动/协议不符：降级，不把整次批改搞挂
            await self.emit(
                "delta",
                f"流式不可用（{type(exc).__name__}: {exc}），已回退一次性批改",
                notice="stream-fallback",
            )
            # 中断前吐出的半句已经发给前端了，但最终文本必须只算回退后的完整评语，
            # 否则 result["text"] 会变成「半句 + 完整评语」的拼接怪
            self.streamed = ""
            raw = await llm.chat_json("review", payload)
            review = coerce_review(raw, question, base=local)
            await self._emit_all(render_review_text(review), config.CHUNK_PACE_SECONDS)
            return review

        parsed = _extract_json(self.streamed)
        if parsed is None:
            await self.emit(
                "delta",
                "模型未按约定输出结构化 JSON，结构化字段已回退本地规则",
                notice="parse-fallback",
            )
            raw = local
        else:
            raw = parsed
        return coerce_review(raw, question, base=local)
