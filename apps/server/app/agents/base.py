"""Agent 基类：只负责发布事件与共享运行上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..events import AGENT_LABELS, Event, EventBus


@dataclass
class RunContext:
    run_id: str
    bus: EventBus
    state: dict[str, Any] = field(default_factory=dict)


class Agent:
    name = "agent"

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    @property
    def label(self) -> str:
        return AGENT_LABELS.get(self.name, self.name)

    async def emit(self, phase: str, message: str = "", **payload: Any) -> None:
        await self.ctx.bus.publish(
            Event(self.ctx.run_id, self.name, phase, message, payload)
        )

    async def run(self) -> Any:
        raise NotImplementedError
