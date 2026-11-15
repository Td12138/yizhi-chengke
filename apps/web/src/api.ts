import type { AgentEvent } from "./types";

// 单次运行的等待上限，避免看板卡死
const RUN_TIMEOUT_MS = 120_000;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${text}`.trim());
  }
  return (await resp.json()) as T;
}

export function getJSON<T>(url: string): Promise<T> {
  return fetch(url).then(handle<T>);
}

export function postJSON<T>(url: string, body: unknown): Promise<T> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(handle<T>);
}

export function postFile<T>(url: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return fetch(url, { method: "POST", body: form }).then(handle<T>);
}

/**
 * 订阅一次运行的事件流；编排器给出 done / error 时结束。
 * 返回最后一条事件，调用方从中取 payload.result。
 */
export function streamRun(
  runId: string,
  onEvent: (event: AgentEvent) => void
): Promise<AgentEvent | null> {
  return new Promise((resolve) => {
    const source = new EventSource(`/api/events/${runId}`);
    let settled = false;

    const finish = (event: AgentEvent | null) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      source.close();
      resolve(event);
    };

    const timer = window.setTimeout(() => finish(null), RUN_TIMEOUT_MS);

    source.onmessage = (message) => {
      let event: AgentEvent;
      try {
        event = JSON.parse(message.data) as AgentEvent;
      } catch {
        return;
      }
      onEvent(event);
      if (
        event.agent === "orchestrator" &&
        (event.phase === "done" || event.phase === "error")
      ) {
        finish(event);
      }
    };
    source.addEventListener("close", () => finish(null));
    source.onerror = () => finish(null);
  });
}
