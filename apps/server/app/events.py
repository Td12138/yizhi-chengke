"""Agent 事件总线：一次运行 = 一个 run，事件顺序落历史，SSE 按序消费并支持中途接入。

v0.1.5 起事件同时落库（开关 EVENT_PERSIST）：run 被回收或服务重启后，
仍可通过 /api/runs/{run_id}/events 回放。落库失败只影响回放，绝不影响 SSE。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator

from . import config, db

# 单次运行保留的事件上限，防止长时间运行占内存
MAX_EVENTS_PER_RUN = 500
# 终止事件：达到上限时也不能丢，否则前端永远等不到 done/error。
# 注意：每个子 Agent 也会发 done，不能把所有 done 都当终止事件，否则上限形同虚设。
TERMINAL_EVENTS = {
    ("orchestrator", "done"),
    ("orchestrator", "error"),
    ("verify", "rejected"),
}
# 保留的运行数上限：超过就回收已结束的 run（SSE 若仍在读，对象仍在内存里，不受影响）
MAX_RUNS = 50
# SSE 保活间隔（秒），避免中间层断流
KEEPALIVE_SECONDS = 15.0

AGENT_LABELS = {
    "recognize": "识别",
    "extract": "萃取",
    "compose": "命题",
    "verify": "质检",
    "explain": "讲解",
    "retrieve": "检索",
    "review": "批改",
    "orchestrator": "编排",
}


@dataclass
class Event:
    run_id: str
    agent: str
    phase: str  # queued | running | delta | done | rejected | error
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        return "data: " + json.dumps(asdict(self), ensure_ascii=False) + "\n\n"


class Run:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.history: list[Event] = []
        self.changed = asyncio.Event()
        self.closed = False
        self.error: str | None = None

    def append(self, event: Event) -> None:
        if (
            len(self.history) < MAX_EVENTS_PER_RUN
            or (event.agent, event.phase) in TERMINAL_EVENTS
        ):
            # 终止事件允许越过上限：若丢弃 / 顶掉旧事件，正在读流的 SSE 客户端
            # 会因下标错位永远收不到 done/error，前端就卡在最后一步
            self.history.append(event)
        self.changed.set()


class EventBus:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, run_id: str) -> Run:
        self._prune()
        if config.EVENT_PERSIST:
            try:
                db.prune_events(config.EVENT_PERSIST_RUNS)
            except Exception:
                pass  # 清理失败不影响新 run；表顶多涨一点
        run = Run(run_id)
        self._runs[run_id] = run
        return run

    def _prune(self) -> None:
        """只回收已结束的 run，保证长时间运行不无限增长。"""
        if len(self._runs) < MAX_RUNS:
            return
        for old_id in [rid for rid, run in self._runs.items() if run.closed]:
            if len(self._runs) < MAX_RUNS:
                break
            del self._runs[old_id]

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def publish(self, event: Event) -> None:
        run = self._runs.get(event.run_id)
        if run is None:
            return
        run.append(event)
        if config.EVENT_PERSIST:
            try:
                db.insert_event(
                    event.run_id,
                    event.agent,
                    event.phase,
                    event.message,
                    event.payload,
                    event.ts,
                )
            except Exception:
                pass  # 落库失败只影响事后回放，SSE 主流程不能被打断

    def close(self, run_id: str, error: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run.error = error
        run.closed = True
        run.changed.set()

    async def stream(self, run_id: str) -> AsyncIterator[str]:
        run = self._runs.get(run_id)
        if run is None:
            yield ': no-such-run\n\n'
            return

        sent = 0
        while True:
            while sent < len(run.history):
                yield run.history[sent].to_sse()
                sent += 1
            if run.closed:
                break
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ': keepalive\n\n'
        yield 'event: close\ndata: {}\n\n'


bus = EventBus()
