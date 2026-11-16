import assert from "node:assert/strict";
import { test } from "node:test";

import { outcomeFromEvents } from "../src/api.ts";
import type { AgentEvent } from "../src/types.ts";

function event(agent: string, phase: AgentEvent["phase"]): AgentEvent {
  return { run_id: "r", agent, phase, message: "", payload: {}, ts: 0 };
}

test("回放合成：空事件返回 null", () => {
  assert.equal(outcomeFromEvents([]), null);
});

test("回放合成：编排器 done 结尾视为完成", () => {
  const events = [event("compose", "done"), event("orchestrator", "done")];
  const outcome = outcomeFromEvents(events);
  assert.ok(outcome);
  assert.equal(outcome.reason, "done");
  assert.equal(outcome.eventCount, 2);
  assert.equal(outcome.last, events[1]);
});

test("回放合成：编排器 error 结尾视为失败", () => {
  const outcome = outcomeFromEvents([event("orchestrator", "error")]);
  assert.equal(outcome?.reason, "error");
});

test("回放合成：子 Agent 的 done 不算结束", () => {
  const outcome = outcomeFromEvents([event("verify", "done")]);
  assert.equal(outcome?.reason, "broken");
});