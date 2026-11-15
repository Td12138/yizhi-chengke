"""识别 Agent：图像 → 结构化版面块（视觉模型）。"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..providers import fixtures, vlm
from .base import Agent


class RecognizeAgent(Agent):
    name = "recognize"

    async def run(self) -> list[dict]:
        await self.emit("running", "读取图像，切分版面")
        image_path = Path(self.ctx.state["image_path"])
        # 上传落盘时文件名已换成 uuid，样例集要按原始文件名判定
        source = self.ctx.state.get("source_name") or image_path.name
        blocks = await vlm.recognize(image_path, source=source)
        self.ctx.state["blocks"] = blocks

        formulas = [b for b in blocks if b.get("type") == "formula"]
        note = (
            f"（{fixtures.match_note(source)}）"
            if config.VLM_PROVIDER == "mock"
            else ""
        )
        await self.emit(
            "delta",
            f"识别到 {len(blocks)} 个版面块，其中公式 {len(formulas)} 个{note}",
            blocks=blocks,
        )
        await self.emit("done", "版面结构化完成")
        return blocks
