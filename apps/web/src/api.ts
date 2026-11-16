import type {
  AgentEvent,
  Card,
  CardPatch,
  CardRevision,
  GraphData,
  SourcesData,
  StatsData,
} from "./types";

/**
 * 单次运行的等待上限，按动作区分：上传识别要读整份文档（最长 10 分钟），
 * 出题要跑命题 + 验算 + 驳回重跑（5 分钟），其余动作 2 分钟足够。
 * 超时只是前端停止等待，后端任务可能仍在跑 —— 文案里要写清楚。
 */
const DEFAULT_RUN_TIMEOUT_MS = 120_000;
const RUN_TIMEOUT_MS: Record<string, number> = {
  ingest: 600_000,
  generate: 300_000,
  grade: DEFAULT_RUN_TIMEOUT_MS,
  review: DEFAULT_RUN_TIMEOUT_MS,
  ask: DEFAULT_RUN_TIMEOUT_MS,
};

/** 按动作取等待上限；未知动作走默认值 */
export function timeoutMsFor(kind: string): number {
  return RUN_TIMEOUT_MS[kind] ?? DEFAULT_RUN_TIMEOUT_MS;
}

/** 进度回调频率（毫秒） */
const PROGRESS_TICK_MS = 1_000;
/** 错误原文保留长度，避免整段堆栈糊满屏幕 */
const MAX_DETAIL_CHARS = 240;
/** 后端不可达时的统一提示（status = 0） */
const OFFLINE_MESSAGE = "无法连接后端服务，请确认服务已启动";

/** 常见状态码的一句话解释，避免界面只剩「HTTP 500」 */
const STATUS_HINT: Record<number, string> = {
  400: "请求参数不被接受",
  404: "资源不存在",
  405: "接口不支持该请求方法",
  409: "状态冲突",
  413: "内容超过大小上限",
  422: "参数校验失败",
  500: "后端内部错误",
  502: "后端不可达",
  503: "后端不可用",
  504: "后端超时",
};

/** 带状态码的接口错误：调用方据此区分「卡片不存在」「服务未启动」等。 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(ApiError.format(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  private static format(status: number, detail: string): string {
    if (status === 0) return detail;
    const hint = STATUS_HINT[status];
    const head = hint ? `HTTP ${status} · ${hint}` : `HTTP ${status}`;
    return detail ? `${head}：${detail}` : head;
  }
}

/** FastAPI 的错误体是 {"detail": "..."}，优先取人话，取不到再退回原文。 */
function summarize(text: string): string {
  if (!text) return "";
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string" && detail) return detail;
    }
  } catch {
    // 非 JSON 错误体，按原文处理
  }
  return text.length > MAX_DETAIL_CHARS
    ? `${text.slice(0, MAX_DETAIL_CHARS)}…`
    : text;
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new ApiError(resp.status, summarize(text));
  }
  return (await resp.json()) as T;
}

/** 统一网络异常：fetch 自身抛错时给一句能看懂的提示。 */
async function request(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new ApiError(0, OFFLINE_MESSAGE);
  }
}

export function getJSON<T>(url: string): Promise<T> {
  return request(url).then(handle<T>);
}

export function postJSON<T>(url: string, body: unknown): Promise<T> {
  return request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(handle<T>);
}

export function patchJSON<T>(url: string, body: unknown): Promise<T> {
  return request(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(handle<T>);
}

export function postFile<T>(url: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return request(url, { method: "POST", body: form }).then(handle<T>);
}

/**
 * 人工修正知识点卡片。
 * 请求体只带改动字段；后端返回更新后的 card 对象，404 表示卡片不存在。
 */
export function updateCard(id: number, patch: CardPatch): Promise<Card> {
  return patchJSON<Card>(`/api/cards/${id}`, patch);
}

/** 人工修正历史：新→旧；旧后端无此路由时调用方按 404/405 静默降级。 */
export function fetchCardRevisions(id: number): Promise<CardRevision[]> {
  return getJSON<{ revisions: CardRevision[] }>(`/api/cards/${id}/revisions`).then(
    (data) => data.revisions ?? []
  );
}

/** 知识谱系图：节点 = 知识点卡片，边 = 共同出现关系。 */
export function fetchGraph(): Promise<GraphData> {
  return getJSON<GraphData>("/api/graph");
}

/** 使用统计：最常被问的知识点与提问 / 出题次数。 */
export function fetchStats(): Promise<StatsData> {
  return getJSON<StatsData>("/api/stats");
}

/** 已归档的原图列表（上传 / 拍照后按识别结果重命名）。 */
export function fetchSources(): Promise<SourcesData> {
  return getJSON<SourcesData>("/api/sources");
}

/** 事件流的结束原因：调用方据此给出可重试的提示，而非静默失败。 */
export type StreamReason = "done" | "error" | "timeout" | "broken";
export type StreamOutcome = {
  /** 最后收到的事件（可能为 null） */
  last: AgentEvent | null;
  reason: StreamReason;
  /** 收到的事件条数，0 说明流根本没起来 */
  eventCount: number;
  /** 本次等待耗时 */
  elapsedMs: number;
};

type StreamOptions = {
  /** 每秒回调一次已等待时长，用于进度提示 */
  onTick?: (elapsedMs: number) => void;
  timeoutMs?: number;
};

/**
 * 订阅一次运行的事件流；编排器给出 done / error 时结束。
 * 断流、超时都返回明确的 reason，调用方负责提示与重试。
 */
export function streamRun(
  runId: string,
  onEvent: (event: AgentEvent) => void,
  options: StreamOptions = {}
): Promise<StreamOutcome> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_RUN_TIMEOUT_MS;

  return new Promise((resolve) => {
    const startedAt = Date.now();
    const source = new EventSource(`/api/events/${runId}`);
    let settled = false;
    let last: AgentEvent | null = null;
    let eventCount = 0;

    const ticker = window.setInterval(() => {
      options.onTick?.(Date.now() - startedAt);
    }, PROGRESS_TICK_MS);

    const timer = window.setTimeout(() => finish("timeout"), timeoutMs);

    function finish(reason: StreamReason) {
      if (settled) return;
      settled = true;
      window.clearInterval(ticker);
      window.clearTimeout(timer);
      source.close();
      resolve({
        last,
        reason,
        eventCount,
        elapsedMs: Date.now() - startedAt,
      });
    }

    source.onmessage = (message) => {
      let event: AgentEvent;
      try {
        event = JSON.parse(message.data) as AgentEvent;
      } catch {
        return;
      }
      last = event;
      eventCount += 1;
      onEvent(event);
      if (
        event.agent === "orchestrator" &&
        (event.phase === "done" || event.phase === "error")
      ) {
        finish(event.phase === "error" ? "error" : "done");
      }
    };

    // 服务端在 done / error 之后会补一条 close；未收到结束事件就 close = 断流
    source.addEventListener("close", () => finish("broken"));
    source.onerror = () => finish("broken");
  });
}
