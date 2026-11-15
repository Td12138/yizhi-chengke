"""讲解 Agent：错因分析 + 选可视化模板与参数。"""

from __future__ import annotations

import math
from typing import Any

from ..providers import llm
from .base import Agent

# 可视化模板参数：缺一个或不是有限数就不出图，只显示文字讲解
PARAM_KEYS = ("a", "b", "c", "x_min", "x_max")
# 字段缺失 / 类型不对时的兜底，保证前端拿到固定形状
DEFAULT_TEMPLATE = "text"
DEFAULT_TITLE = "错题讲解"


def _text(value: Any, default: str = "") -> str:
    """只收非空字符串；其他类型（None / 数字 / 对象）一律退回默认值。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _params(raw: Any) -> dict[str, float] | None:
    """把模型给的 params 收敛成五个有限浮点数；任一不合法就整块丢弃。

    真实模型常把 a 写成字符串、漏掉 x_max，或给出 NaN / 反向区间。
    这些数据交给前端画图会得到空白或崩图，不如明确降级成「只有文字」。
    """
    if not isinstance(raw, dict):
        return None
    values: dict[str, float] = {}
    for key in PARAM_KEYS:
        value = raw.get(key)
        # bool 是 int 的子类，True 会被 float() 收成 1.0，这里明确排除
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number):
            return None
        values[key] = number
    # 区间长度必须为正：等宽 / 反向会让画图的 x 比例除零或翻转
    if values["x_max"] <= values["x_min"]:
        return None
    return values


def coerce_explain(raw: Any) -> dict:
    """讲解结果结构归一：字段缺省可读，params 不合法就不下发。"""
    data = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {
        "template": _text(data.get("template"), DEFAULT_TEMPLATE),
        "title": _text(data.get("title"), DEFAULT_TITLE),
        "script": _text(data.get("script")),
    }
    params = _params(data.get("params"))
    if params is not None:
        result["params"] = params
    return result


class ExplainAgent(Agent):
    name = "explain"

    async def run(self) -> dict:
        question = self.ctx.state.get("question", {})
        user_answer = self.ctx.state.get("user_answer", "")
        await self.emit("running", "分析错因，选择讲解方式")
        raw = await llm.chat_json(
            "explain", {"question": question, "user_answer": user_answer}
        )
        result = coerce_explain(raw)
        self.ctx.state["explain"] = result
        await self.emit(
            "delta",
            f"讲解模板：{result.get('template', '文本')}",
            explain=result,
        )
        await self.emit("done", "讲解脚本已生成")
        return result
