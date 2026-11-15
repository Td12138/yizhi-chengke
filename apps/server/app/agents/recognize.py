"""识别 Agent：图像 → 结构化版面块（视觉模型）。"""

from __future__ import annotations

from pathlib import Path

from ..providers import vlm
from .base import Agent


class RecognizeAgent(Agent):
    name = "recognize"

    async def run(self) -> list[dict]:
        await self.emit("running", "读取图像，切分版面")
        blocks = await vlm.recognize(Path(self.ctx.state["image_path"]))
        self.ctx.state["blocks"] = blocks

        formulas = [b for b in blocks if b.get("type") == "formula"]
        await self.emit(
            "delta",
            f"识别到 {len(blocks)} 个版面块，其中公式 {len(formulas)} 个",
            blocks=blocks,
        )
        await self.emit("done", "版面结构化完成")
        return blocks
