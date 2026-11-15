"""讲解 Agent：错因分析 + 选可视化模板与参数。"""

from __future__ import annotations

from ..providers import llm
from .base import Agent


class ExplainAgent(Agent):
    name = "explain"

    async def run(self) -> dict:
        question = self.ctx.state.get("question", {})
        user_answer = self.ctx.state.get("user_answer", "")
        await self.emit("running", "分析错因，选择讲解方式")
        result = await llm.chat_json(
            "explain", {"question": question, "user_answer": user_answer}
        )
        self.ctx.state["explain"] = result
        await self.emit(
            "delta",
            f"讲解模板：{result.get('template', '文本')}",
            explain=result,
        )
        await self.emit("done", "讲解脚本已生成")
        return result
