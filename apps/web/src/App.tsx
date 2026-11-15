import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import AgentBoard, { AGENTS } from "./components/AgentBoard";
import CameraCapture from "./components/CameraCapture";
import CardEditor from "./components/CardEditor";
import FunctionPlot from "./components/FunctionPlot";
import FunctionSlider from "./components/FunctionSlider";
import KnowledgeGraph from "./components/KnowledgeGraph";
import MathText from "./components/MathText";
import ReviewPanel from "./components/ReviewPanel";
import RunProgress from "./components/RunProgress";
import SourceLightbox from "./components/SourceLightbox";
import SourceStrip from "./components/SourceStrip";
import TourGuide, { shouldAutoStartTour } from "./components/TourGuide";
import type { Board, RunInfo, Stage } from "./components/RunProgress";
import {
  fetchGraph,
  fetchSources,
  fetchStats,
  getJSON,
  postFile,
  postJSON,
  streamRun,
  timeoutMsFor,
} from "./api";
import type {
  AgentEvent,
  AskAnswer,
  Card,
  Explain,
  GraphData,
  Question,
  ReviewBetterMethod,
  ReviewFeed,
  ReviewFirstError,
  ReviewResult,
  ReviewStep,
  ReviewVerdict,
  SourceItem,
  StatsData,
  Status,
} from "./types";

const AGENT_LABEL: Record<string, string> = {
  ...Object.fromEntries(AGENTS.map((agent) => [agent.key, agent.label])),
  orchestrator: "编排",
};
const LETTERS = "ABCDEFGH";
const MAX_LOGS = 200;
const NOTICE_MS = 4_000;
/** 生成题数范围：与后端 /api/generate 的约束一致，输入框与提交都夹在这个区间 */
const MIN_QUESTIONS = 1;
const MAX_QUESTIONS = 12;
/** 「最常被问」列表条数，与后端 /api/stats 的默认返回量对齐 */
const TOP_ASKED = 10;
/** 卡片区最多显示几张来源原图缩略（面板窄，多了要横向滚动） */
const CARD_SOURCE_LIMIT = 4;
/** 知识库图册展示最近多少张来源原图 */
const GALLERY_SOURCE_LIMIT = 12;
/** 练习题错位淡入的间隔，上限 400ms 防止长卷等待过久 */
const QUESTION_STAGGER_MS = 40;
const QUESTION_STAGGER_MAX_MS = 400;

/** 示例板书：public/samples 下的静态图，点击后复用正常上传流程 */
const SAMPLES = [
  {
    label: "二次函数图象",
    file: "2026-11-15-二次函数图象与性质.png",
    url: "/samples/sample-quadratic.png",
  },
  {
    label: "一元二次方程求根",
    file: "2026-11-15-一元二次方程求根.png",
    url: "/samples/sample-equation.png",
  },
  {
    label: "三角函数图像",
    file: "2026-11-15-三角函数图像要点.png",
    url: "/samples/sample-trig.png",
  },
];

const ACTION_LABEL: Record<string, string> = {
  ingest: "上传识别",
  generate: "生成练习卷",
  grade: "判分讲解",
  review: "AI 批改",
  ask: "追问检索",
};

/** 每个动作的阶段划分，用于进度条；agents 为空表示纯客户端环节 */
const STAGES: Record<string, Stage[]> = {
  ingest: [
    { label: "上传图片", agents: [] },
    { label: "识别版面", agents: ["recognize"] },
    { label: "萃取知识点", agents: ["extract"] },
    { label: "卡片入库", agents: ["orchestrator"] },
  ],
  generate: [
    { label: "命题", agents: ["compose"] },
    { label: "质检验算", agents: ["verify"] },
    { label: "交付练习卷", agents: ["orchestrator"] },
  ],
  grade: [
    { label: "判定对错", agents: ["orchestrator"] },
    { label: "错因讲解", agents: ["explain"] },
  ],
  review: [
    { label: "逐点批改", agents: ["review"] },
    { label: "汇总建议", agents: ["orchestrator"] },
  ],
  ask: [
    { label: "检索卡片", agents: ["retrieve"] },
    { label: "组织回答", agents: ["orchestrator"] },
  ],
};

/** 流式占位 id 的起点：done 之前题目/卡片可能还没有库 id，用负数占位（真实 id 恒为正） */
const FIRST_TEMP_ID = 0;

/**
 * 上传约束：与后端 config.ALLOWED_SUFFIXES / DOC_SUFFIXES / MAX_*_BYTES 对齐。
 * 后端改后缀或上限时，这里要同步，否则会在前端误拦或漏拦。
 */
const UPLOAD_ACCEPT = "image/*,.pdf,.docx,.txt,.md";
const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp", ".bmp"];
const DOC_SUFFIXES = [".pdf", ".docx", ".txt", ".md"];
const MB = 1024 * 1024;
/** 图片上限 12MB（后端 MAX_UPLOAD_BYTES） */
const MAX_IMAGE_BYTES = 12 * MB;
/** 文档上限 20MB（后端 MAX_DOC_BYTES） */
const MAX_DOC_BYTES = 20 * MB;
const MAX_IMAGE_MB = MAX_IMAGE_BYTES / MB;
const MAX_DOC_MB = MAX_DOC_BYTES / MB;
const UPLOAD_FORMATS_HINT = "图片或 PDF/DOCX/TXT/MD";

type LogItem = { seq: number; agent: string; phase: Status; message: string };
type RunError = { step: string; message: string; retry: boolean };

/** 上传预览：图片走缩略图，文档只显示文件名 + 类型徽标 */
type FilePreview = { url: string; name: string; kind: "image" | "document" };
type UploadCheck =
  | { ok: true; kind: "image" | "document" }
  | { ok: false; message: string };

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** 等待上限（秒）：与 api.ts 的按动作超时表同源，只用于文案展示 */
function timeoutSecOf(kind: string): number {
  return Math.round(timeoutMsFor(kind) / 1000);
}

/** 超时兜底文案：只有入库类动作刷新后能回来，判分/批改/追问不会自动保存 */
function timeoutHintFor(kind: string): string {
  if (kind === "ingest" || kind === "generate") {
    return "后端可能仍在处理，稍后刷新可见；也可重试，反复超时请查看后端日志。";
  }
  return "后端可能仍在处理，可直接重试；本次结果不会自动保存，反复超时请查看后端日志。";
}

/** 题数夹到 1–12：输入框清空（NaN）、手打 99 都不越界 */
function clampCount(value: number): number {
  if (!Number.isFinite(value)) return MIN_QUESTIONS;
  return Math.min(MAX_QUESTIONS, Math.max(MIN_QUESTIONS, Math.round(value)));
}

/** 小写后缀（含点）；没有扩展名返回空串 */
function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** 文档预览的徽标文字：PDF / DOCX / TXT / MD */
function fileBadge(name: string): string {
  return suffixOf(name).replace(".", "").toUpperCase() || "DOC";
}

/** 提交前校验：格式与大小都在前端拦下，给出明确原因，不等后端 400/413 */
function checkUpload(file: File): UploadCheck {
  const suffix = suffixOf(file.name);
  const isImage = IMAGE_SUFFIXES.includes(suffix);
  const isDoc = DOC_SUFFIXES.includes(suffix);
  if (!isImage && !isDoc) {
    return {
      ok: false,
      message: suffix
        ? `不支持 ${suffix}，请上传${UPLOAD_FORMATS_HINT}`
        : `文件「${file.name}」没有扩展名，请上传${UPLOAD_FORMATS_HINT}`,
    };
  }
  if (isDoc && file.size > MAX_DOC_BYTES) {
    return {
      ok: false,
      message: `文件过大（${(file.size / MB).toFixed(1)}MB）：文档上限 ${MAX_DOC_MB}MB，请压缩后再试`,
    };
  }
  if (isImage && file.size > MAX_IMAGE_BYTES) {
    return {
      ok: false,
      message: `图片过大（${(file.size / MB).toFixed(1)}MB）：图片上限 ${MAX_IMAGE_MB}MB，请压缩后再试`,
    };
  }
  return { ok: true, kind: isDoc ? "document" : "image" };
}

/**
 * 出题流里的单题：delta 的 payload.question 可能只有部分字段，
 * 缺什么补什么，避免渲染阶段因空值崩掉。
 */
function normalizeStreamedQuestion(raw: any, tempId: number): Question {
  const options = Array.isArray(raw?.options)
    ? raw.options.map((item: unknown) => String(item))
    : [];
  return {
    id: typeof raw?.id === "number" && raw.id > 0 ? raw.id : tempId,
    stem_latex: String(raw?.stem_latex ?? ""),
    qtype: raw?.qtype === "choice" ? "choice" : "fill",
    options,
    answer: String(raw?.answer ?? ""),
    solution: String(raw?.solution ?? ""),
    difficulty: String(raw?.difficulty ?? ""),
    verified: Number(raw?.verified ?? 0) || 0,
    verify_log: String(raw?.verify_log ?? ""),
  };
}

/** 卡片流里的单卡：标签后端可能给数组，统一成逗号串（与 /api/cards 一致） */
function normalizeStreamedCard(raw: any, tempId: number): Card {
  const tags = Array.isArray(raw?.tags)
    ? raw.tags.map((item: unknown) => String(item)).join(",")
    : String(raw?.tags ?? "");
  return {
    id: typeof raw?.id === "number" && raw.id > 0 ? raw.id : tempId,
    title: String(raw?.title ?? "（识别中）"),
    summary: String(raw?.summary ?? ""),
    subject: String(raw?.subject ?? ""),
    tags,
    formula: String(raw?.formula ?? ""),
    body: String(raw?.body ?? ""),
  };
}

/** 分步清单：字段缺 / 形状不对就丢掉那一条，index 缺失时按顺序补 */
function readReviewSteps(raw: any): ReviewStep[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((step: any) => step && typeof step.expect === "string" && step.expect)
    .map((step: any, position: number) => {
      const index = Number(step.index);
      return {
        index: Number.isFinite(index) && index > 0 ? Math.trunc(index) : position + 1,
        expect: step.expect,
        found: Boolean(step.found),
        note: typeof step.note === "string" ? step.note : "",
      };
    });
}

function readFirstError(raw: any): ReviewFirstError | null {
  if (!raw || typeof raw !== "object") return null;
  const step = Number(raw.step);
  if (!Number.isFinite(step) || step <= 0) return null;
  return {
    step: Math.trunc(step),
    reason: typeof raw.reason === "string" ? raw.reason : "",
  };
}

function readBetterMethod(raw: any): ReviewBetterMethod | null {
  if (!raw || typeof raw !== "object") return null;
  const name = typeof raw.name === "string" ? raw.name.trim() : "";
  const why = typeof raw.why === "string" ? raw.why : "";
  const steps = Array.isArray(raw.steps)
    ? raw.steps.map((item: unknown) => String(item)).filter((item) => item.trim() !== "")
    : [];
  // 名称与步骤都空时视为没有更优解法，避免出现空卡片
  if (!name && steps.length === 0) return null;
  return { name: name || "推荐解法", why, steps };
}

/** 批改结论：缺字段补空；score 不是数字视为没拿到结论（调用方据此报错重试） */
function readReviewResult(raw: any): ReviewResult | null {
  if (!raw || typeof raw !== "object") return null;
  const score = Number(raw.score);
  if (!Number.isFinite(score)) return null;
  const verdict: ReviewVerdict | undefined =
    raw.verdict === "correct" || raw.verdict === "partial" || raw.verdict === "wrong"
      ? raw.verdict
      : undefined;
  const points = Array.isArray(raw.points)
    ? raw.points
        .filter((point: any) => point && typeof point.text === "string")
        .map((point: any) => ({ hit: Boolean(point.hit), text: point.text }))
    : [];
  const issues = Array.isArray(raw.issues)
    ? raw.issues.map((issue: unknown) => String(issue))
    : [];
  return {
    score,
    verdict,
    points,
    issues,
    suggestion: typeof raw.suggestion === "string" ? raw.suggestion : "",
    reference: typeof raw.reference === "string" ? raw.reference : "",
    steps: readReviewSteps(raw.steps),
    first_error: readFirstError(raw.first_error),
    better_method: readBetterMethod(raw.better_method),
    method_comparison:
      typeof raw.method_comparison === "string" ? raw.method_comparison : "",
  };
}

export default function App() {
  const [cards, setCards] = useState<Card[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editedIds, setEditedIds] = useState<number[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [verifyRejects, setVerifyRejects] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [results, setResults] = useState<Record<number, { correct: boolean }>>({});
  /** 单题 AI 批改面板：key = 题目 id */
  const [reviews, setReviews] = useState<Record<number, ReviewFeed>>({});
  const [explain, setExplain] = useState<{ qid: number; data: Explain } | null>(null);
  const [board, setBoard] = useState<Board>({});
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState<RunError | null>(null);
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [plotExpression, setPlotExpression] = useState("x^2 - 4x + 3");
  const [requirement, setRequirement] = useState("给我出 5 道这页的变式题，中等难度");
  const [count, setCount] = useState(5);
  const [askQuery, setAskQuery] = useState("");
  const [askAnswer, setAskAnswer] = useState<AskAnswer | null>(null);
  const [health, setHealth] = useState<{
    vlm_provider: string;
    llm_provider: string;
  } | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [sourcesError, setSourcesError] = useState("");
  const [lightbox, setLightbox] = useState<SourceItem | null>(null);
  /** 正在加载的示例图 URL，空串表示没有加载中的示例 */
  const [sampleLoading, setSampleLoading] = useState("");
  /** 首次打开的新手引导开关（?tour=1 或 localStorage 没有完成标记时自动开） */
  const [tourOpen, setTourOpen] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLLIElement>(null);
  const seq = useRef(0);
  const retryRef = useRef<(() => void) | null>(null);
  const noticeTimer = useRef<number | undefined>(undefined);
  const previewUrl = useRef("");
  /** 图谱请求序号：只认最后一次，避免连点刷新时旧响应覆盖新数据 */
  const graphReq = useRef(0);
  /** 流式占位 id 计数器（递减，恒为负） */
  const tempSeq = useRef(FIRST_TEMP_ID);
  /** 当前占用看板 / 进度条的 run：后发起的运行接管，旧 run 的迟到事件不再改看板 */
  const activeRun = useRef("");
  /** 进度条归属令牌：只让最后发起的那次运行改写进度条状态 */
  const runToken = useRef(0);

  /**
   * 拉取知识谱系、统计与归档原图。
   * 供四处调用：进入页面、上传识别后、追问 / 出题成功后、面板刷新按钮。
   */
  const loadKnowledge = useCallback(async () => {
    const token = graphReq.current + 1;
    graphReq.current = token;
    setGraphLoading(true);
    setGraphError("");
    setSourcesError("");
    const [knowledgeResult, sourcesResult] = await Promise.allSettled([
      Promise.all([fetchGraph(), fetchStats()]),
      fetchSources(),
    ]);
    if (graphReq.current !== token) return;

    if (knowledgeResult.status === "fulfilled") {
      setGraph(knowledgeResult.value[0]);
      setStats(knowledgeResult.value[1]);
    } else {
      setGraphError(describeError(knowledgeResult.reason));
    }

    if (sourcesResult.status === "fulfilled") {
      setSources(sourcesResult.value.sources ?? []);
    } else {
      setSourcesError(describeError(sourcesResult.reason));
    }

    setGraphLoading(false);
  }, []);

  useEffect(() => {
    void loadKnowledge();
  }, [loadKnowledge]);

  useEffect(() => {
    getJSON<{ cards: Card[] }>("/api/cards")
      // 首屏数据晚到：已有运行在跑时不要用旧库数据覆盖流式追加的卡片
      .then((data) => {
        if (runToken.current !== 0) return;
        setCards(data.cards);
      })
      .catch(() => undefined);
    getJSON<{ questions: Question[] }>("/api/questions")
      .then((data) => {
        if (runToken.current !== 0) return;
        setQuestions(data.questions);
      })
      .catch(() => undefined);
    getJSON<{ vlm_provider: string; llm_provider: string }>("/api/health")
      .then(setHealth)
      .catch(() => undefined);
    return () => {
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      window.clearTimeout(noticeTimer.current);
    };
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  // 首次打开自动进引导；?tour=1 时无视完成标记强制展示一次
  useEffect(() => {
    if (shouldAutoStartTour()) setTourOpen(true);
  }, []);

  const closeTour = useCallback(() => setTourOpen(false), []);

  // 打开编辑后把卡片滚进视野（长列表里点下面卡片的场景）
  useEffect(() => {
    if (editingId !== null) {
      editorRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [editingId]);

  function flash(text: string) {
    setNotice(text);
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(""), NOTICE_MS);
  }

  /** 统一失败出口：错误横幅写明哪一步 + 进度条标红，保留重试入口 */
  function failRun(step: string, message: string, token?: number) {
    setError({ step, message, retry: true });
    if (token === undefined || runToken.current === token) {
      setRun((prev) => (prev ? { ...prev, failed: true } : prev));
    }
  }

  /** 流式追加一道题：负数占位 id 防撞车，真 id 重复则跳过 */
  function appendQuestion(raw: any) {
    tempSeq.current -= 1;
    const incoming = normalizeStreamedQuestion(raw, tempSeq.current);
    setQuestions((prev) => {
      if (incoming.id > 0 && prev.some((question) => question.id === incoming.id)) {
        return prev;
      }
      return [...prev, incoming];
    });
  }

  /** 流式追加一张卡片：真 id 重复则跳过 */
  function appendCard(raw: any) {
    tempSeq.current -= 1;
    const incoming = normalizeStreamedCard(raw, tempSeq.current);
    setCards((prev) => {
      if (incoming.id > 0 && prev.some((card) => card.id === incoming.id)) {
        return prev;
      }
      return [...prev, incoming];
    });
  }

  /**
   * 流式增量：compose 的 delta 带 payload.question、extract 的带 payload.card，
   * 收到一条追加一条（done 时再用完整结果校正）。
   * 旧后端 delta 里是 questions / cards 数组，这里保持原样忽略，不改变行为。
   */
  function applyStreamDelta(event: AgentEvent) {
    if (event.phase !== "delta" || !event.payload) return;
    if (event.agent === "compose" && event.payload.question) {
      appendQuestion(event.payload.question);
    }
    if (event.agent === "extract" && event.payload.card) {
      appendCard(event.payload.card);
    }
  }

  function handleEvent(event: AgentEvent) {
    const message = event.message ?? "";
    // 看板只认最后一次发起的运行，避免并行运行的旧事件覆盖当前进度
    if (event.run_id === activeRun.current) {
      setBoard((prev) => ({
        ...prev,
        [event.agent]: { phase: event.phase, message },
      }));
    }
    // 命题开始（含质检驳回后的重跑）：清掉上一卷 / 上一轮草稿，新题从 delta 逐道追加
    if (event.agent === "compose" && event.phase === "running") {
      setQuestions([]);
    }
    applyStreamDelta(event);
    // 记录质检驳回轮数：展示在练习卷头部，作为「答案可验证」的可见证据
    if (event.agent === "verify" && event.phase === "rejected") {
      setVerifyRejects((prev) => prev + 1);
    }
    // 批改的逐片文本已经在题卡里看得到，再往日志里灌一条「delta」只会刷屏
    const isReviewChunk =
      event.agent === "review" &&
      event.phase === "delta" &&
      typeof event.payload?.chunk === "string" &&
      event.payload.chunk !== "";
    if (isReviewChunk) return;
    seq.current += 1;
    setLogs((prev) =>
      [
        ...prev,
        { seq: seq.current, agent: event.agent, phase: event.phase, message },
      ].slice(-MAX_LOGS)
    );
  }

  /**
   * 一次运行：发起任务 → 订阅事件流 → 处理结果。
   * 出错时写明是哪一步，并保留重试入口（不静默失败）。
   * `before` 在拿到 run_id 之后、事件流开始之前执行（如清空上一轮流式结果）。
   */
  async function execute(
    kind: string,
    start: () => Promise<{ run_id: string }>,
    after?: (last: AgentEvent | null) => void | Promise<void>,
    before?: () => void
  ) {
    if (busy) return;
    const label = ACTION_LABEL[kind] ?? kind;
    const timeoutMs = timeoutMsFor(kind);
    const token = runToken.current + 1;
    runToken.current = token;

    setBusy(kind);
    setError(null);
    setNotice("");
    setBoard({});
    setRun({
      kind,
      label,
      active: true,
      failed: false,
      elapsedMs: 0,
    });
    retryRef.current = () => {
      void execute(kind, start, after, before);
    };

    try {
      let runId: string;
      try {
        const started = await start();
        runId = started.run_id;
      } catch (err) {
        failRun(`${label} · 发起任务`, describeError(err), token);
        return;
      }

      activeRun.current = runId;
      before?.();
      const outcome = await streamRun(runId, handleEvent, {
        timeoutMs,
        onTick: (elapsedMs) => {
          if (runToken.current !== token) return;
          setRun((prev) => (prev && prev.active ? { ...prev, elapsedMs } : prev));
        },
      });

      if (outcome.reason === "timeout") {
        failRun(
          `${label} · 等待结果`,
          `超过 ${timeoutSecOf(kind)} 秒仍未收到结束事件，已停止等待。${timeoutHintFor(kind)}`,
          token
        );
        return;
      }

      if (outcome.reason === "broken") {
        failRun(
          `${label} · 事件流`,
          outcome.eventCount === 0
            ? "没有收到任何事件（后端未响应或任务已结束），请重试。"
            : `事件流提前中断：已收到 ${outcome.eventCount} 条事件但缺少结束标记，结果可能不完整，可重试。`,
          token
        );
        return;
      }

      if (outcome.reason === "error" || outcome.last?.phase === "error") {
        const who = outcome.last
          ? AGENT_LABEL[outcome.last.agent] ?? outcome.last.agent
          : "未知环节";
        failRun(
          `${label} · ${who}阶段`,
          outcome.last?.message || "任务以错误状态结束",
          token
        );
        return;
      }

      if (after) {
        try {
          await after(outcome.last);
        } catch (err) {
          failRun(`${label} · 加载结果`, describeError(err), token);
        }
      }
    } finally {
      setBusy(null);
      if (runToken.current === token) {
        setRun((prev) => (prev ? { ...prev, active: false } : prev));
      }
    }
  }

  const resultOf = (last: AgentEvent | null) => last?.payload?.result ?? null;

  async function ingest(file: File) {
    // 先做后缀 / 大小校验：不支持的类型直接说清楚，不把文件发出去等 400
    const check = checkUpload(file);
    if ("message" in check) {
      setError({ step: "上传识别 · 文件检查", message: check.message, retry: false });
      return;
    }

    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = "";
    if (check.kind === "image") {
      const url = URL.createObjectURL(file);
      previewUrl.current = url;
      setPreview({ url, name: file.name, kind: "image" });
    } else {
      // 文档不做缩略图（PDF 塞进 <img> 只会显示破图），只保留文件名
      setPreview({ url: "", name: file.name, kind: "document" });
    }

    await execute(
      "ingest",
      () => postFile<{ run_id: string }>("/api/ingest", file),
      async () => {
        const data = await getJSON<{ cards: Card[] }>("/api/cards");
        setCards(data.cards);
        setEditingId(null);
        flash(`已入库，当前共 ${data.cards.length} 张卡片`);
        // 新卡片会带出新节点 / 新边
        void loadKnowledge();
      }
    );
  }

  /** 示例板书：取静态图 → 包成 File → 走同一条 ingest 流程 */
  async function loadSample(sample: (typeof SAMPLES)[number]) {
    if (busy !== null || sampleLoading) return;
    setSampleLoading(sample.url);
    setError(null);
    try {
      const response = await fetch(sample.url);
      if (!response.ok) {
        throw new Error(`示例图加载失败（HTTP ${response.status}）`);
      }
      const blob = await response.blob();
      const file = new File([blob], sample.file, {
        type: blob.type || "image/png",
      });
      await ingest(file);
    } catch (err) {
      setError({
        step: `示例板书 · ${sample.label}`,
        message:
          err instanceof TypeError
            ? "示例图下载失败，请检查网络或静态资源是否可访问"
            : describeError(err),
        retry: false,
      });
    } finally {
      setSampleLoading("");
    }
  }

  async function generate() {
    const trimmed = requirement.trim();
    if (!trimmed) {
      flash("先在输入框里写一句出题要求");
      return;
    }
    await execute(
      "generate",
      () =>
        postJSON<{ run_id: string }>("/api/generate", {
          card_ids: cards.map((card) => card.id),
          requirement: trimmed,
          count: clampCount(count),
        }),
      (last) => {
        const result = resultOf(last);
        if (!result?.questions?.length) {
          throw new Error("返回结果里没有题目（可能全部被质检驳回），请调整需求后重试");
        }
        // done 带完整结果时以它为准做一次校正（去掉驳回题、补漏、去重）
        setQuestions(result.questions);
        setAnswers({});
        setResults({});
        setReviews({});
        setExplain(null);
        // 出题计数变了，重拉谱系让节点大小 / 颜色跟着变
        void loadKnowledge();
      },
      () => {
        // 作答 / 批改状态跟着新卷一起清空；题目本身在命题开始时清空（见 handleEvent）
        setAnswers({});
        setResults({});
        setReviews({});
        setExplain(null);
        // 新一套卷从 0 开始记驳回轮数，避免叠加上一套的「驳回 N 轮」
        setVerifyRejects(0);
      }
    );
  }

  async function grade(questionId: number) {
    await execute(
      "grade",
      () =>
        postJSON<{ run_id: string }>("/api/grade", {
          question_id: questionId,
          user_answer: answers[questionId] ?? "",
        }),
      (last) => {
        const result = resultOf(last);
        if (!result || typeof result.correct !== "boolean") {
          throw new Error("判分结果缺少 correct 字段");
        }
        setResults((prev) => ({
          ...prev,
          [questionId]: { correct: result.correct },
        }));
        if (result.correct) {
          flash(`第 ${questionId} 题答对了`);
        } else if (result.explain) {
          setExplain({ qid: questionId, data: result.explain });
        }
      }
    );
  }

  /** 批改失败：只更新该题面板 + 进度条标红，不弹顶部错误横幅（重试入口在面板里） */
  function failReview(questionId: number, message: string, token: number) {
    setReviews((prev) => {
      const current = prev[questionId];
      // 已经失败过就保留第一条原因，避免被后续通用错误覆盖
      if (!current || current.status === "failed") return prev;
      return {
        ...prev,
        [questionId]: { status: "failed", text: current.text, error: message },
      };
    });
    if (runToken.current === token) {
      setRun((prev) => (prev ? { ...prev, failed: true } : prev));
    }
  }

  /**
   * 单题 AI 批改：POST /api/review 拿 run_id → 复用事件流。
   * review 的 delta.chunk 逐条追加成流式文本，结论取 orchestrator/done 的 result。
   * 与判分互不阻塞：只禁用该题的两个按钮，可同时批改不同题目。
   */
  async function reviewQuestion(questionId: number) {
    if (questionId <= 0) return;
    if (busy) return;
    if (reviews[questionId]?.status === "running") return;

    const label = ACTION_LABEL.review;
    const timeoutMs = timeoutMsFor("review");
    const token = runToken.current + 1;
    runToken.current = token;

    // 同一题重复批改：先清掉上一次的结论与文本，只留「批改中…」
    setReviews((prev) => ({
      ...prev,
      [questionId]: { status: "running", text: "" },
    }));
    setNotice("");
    setRun({ kind: "review", label, active: true, failed: false, elapsedMs: 0 });

    try {
      let runId: string;
      try {
        const started = await postJSON<{ run_id: string }>("/api/review", {
          question_id: questionId,
          user_answer: answers[questionId] ?? "",
        });
        runId = started.run_id;
      } catch (err) {
        failReview(questionId, `发起批改失败：${describeError(err)}`, token);
        return;
      }

      activeRun.current = runId;
      setBoard({});
      const outcome = await streamRun(
        runId,
        (event) => {
          handleEvent(event);
          if (event.agent !== "review") return;
          if (event.phase === "delta") {
            // 流式中断后服务端会回退成完整评语；先把已显示的半句清掉，
            // 否则 UI 会看到「中断前半句 + 完整评语」粘在一起。
            if (event.payload?.notice === "stream-fallback") {
              setReviews((prev) => {
                const current = prev[questionId];
                if (!current || current.status !== "running") return prev;
                return { ...prev, [questionId]: { ...current, text: "" } };
              });
              setNotice(event.message || "流式批改中断，已回退为一次性批改");
              return;
            }
            const chunk =
              typeof event.payload?.chunk === "string" ? event.payload.chunk : "";
            if (!chunk) return;
            setReviews((prev) => {
              const current = prev[questionId];
              if (!current || current.status !== "running") return prev;
              return {
                ...prev,
                [questionId]: { ...current, text: current.text + chunk },
              };
            });
          } else if (event.phase === "error") {
            failReview(questionId, event.message || "批改 Agent 报错", token);
          }
        },
        {
          timeoutMs,
          onTick: (elapsedMs) => {
            if (runToken.current !== token) return;
            setRun((prev) =>
              prev && prev.active ? { ...prev, elapsedMs } : prev
            );
          },
        }
      );

      if (outcome.reason === "timeout") {
        failReview(
          questionId,
          `超过 ${timeoutSecOf("review")} 秒仍未返回批改结果，已停止等待。${timeoutHintFor("review")}`,
          token
        );
        return;
      }
      if (outcome.reason === "broken") {
        failReview(
          questionId,
          outcome.eventCount === 0
            ? "没有收到批改事件（后端未响应或任务已结束），可重试"
            : `批改事件流中断：只收到 ${outcome.eventCount} 条事件，结果可能不完整（可重试）`,
          token
        );
        return;
      }
      if (outcome.reason === "error") {
        failReview(
          questionId,
          outcome.last?.message || "批改以错误状态结束",
          token
        );
        return;
      }

      const result = readReviewResult(outcome.last?.payload?.result);
      if (!result) {
        failReview(questionId, "批改结束但没有返回结构化结论（缺少 result）", token);
        return;
      }
      setReviews((prev) => ({
        ...prev,
        [questionId]: {
          status: "ok",
          text: prev[questionId]?.text ?? "",
          result,
        },
      }));
    } finally {
      if (runToken.current === token) {
        setRun((prev) => (prev ? { ...prev, active: false } : prev));
      }
    }
  }

  async function ask() {
    await execute(
      "ask",
      () => postJSON<{ run_id: string }>("/api/ask", { query: askQuery }),
      (last) => {
        const result = resultOf(last);
        if (!result?.answer) throw new Error("回答结果为空");
        setAskAnswer(result as AskAnswer);
        // 追问计数变了，重拉谱系
        void loadKnowledge();
      }
    );
  }

  function handleCardSaved(next: Card) {
    setCards((prev) =>
      prev.map((card) => (card.id === next.id ? { ...card, ...next } : card))
    );
    setEditedIds((prev) => (prev.includes(next.id) ? prev : [...prev, next.id]));
    setEditingId(null);
    flash(`卡片 #${next.id} 已修正并回写`);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (busy) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void ingest(file);
  }

  const busyLabel = busy ? ACTION_LABEL[busy] ?? busy : "";
  const stages = run ? STAGES[run.kind] ?? [] : [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">纸</span>
          <div>
            <h1>一纸成课</h1>
            <p>拍下一页纸，变成可追问、可练习、可讲解的课</p>
          </div>
        </div>
        <div className="providers">
          <span className={`pill ${health?.vlm_provider === "mock" ? "pill-mock" : "pill-live"}`}>
            VLM {health?.vlm_provider ?? "…"}
          </span>
          <span className={`pill ${health?.llm_provider === "mock" ? "pill-mock" : "pill-live"}`}>
            LLM {health?.llm_provider ?? "…"}
          </span>
          {busy && <span className="pill pill-busy">运行中 · {busyLabel}</span>}
        </div>
      </header>

      {error && (
        <div className="error" role="alert">
          <div className="error-head">
            <strong>失败于：{error.step}</strong>
            {error.retry && (
              <button
                className="btn btn-sm btn-retry"
                disabled={busy !== null}
                onClick={() => retryRef.current?.()}
              >
                重试这一步
              </button>
            )}
          </div>
          <div className="error-msg">{error.message}</div>
        </div>
      )}

      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}

      <main className="layout">
        <section className="panel panel-input">
          <h2>1 · 拍一页纸</h2>
          <div
            className={`dropzone ${busy === "ingest" ? "dropzone-busy" : ""}`}
            data-tour="upload"
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
            {preview ? (
              preview.kind === "image" ? (
                <img src={preview.url} alt={`已选择：${preview.name}`} />
              ) : (
                <div className="dropzone-file">
                  <span className="file-badge" aria-hidden="true">
                    {fileBadge(preview.name)}
                  </span>
                  <span className="file-name" title={preview.name}>
                    {preview.name}
                  </span>
                </div>
              )
            ) : (
              <p className="dropzone-hint">
                拖入图片或 PDF / DOCX 文件，也可以点下方按钮选择
              </p>
            )}
            {busy === "ingest" && (
              <p className="dropzone-status" role="status">
                上传并识别中… 看板第 2 栏显示进度
              </p>
            )}
            <div className="dropzone-actions">
              <label className={`btn ${busy ? "btn-disabled" : ""}`}>
                {busy === "ingest" ? "识别中…" : "选择文件"}
                <input
                  type="file"
                  accept={UPLOAD_ACCEPT}
                  hidden
                  disabled={busy !== null}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) void ingest(file);
                  }}
                />
              </label>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy !== null}
                onClick={() => setCameraOpen(true)}
              >
                打开摄像头
              </button>
            </div>
          </div>

          <div className="samples">
            <span className="samples-label">或用示例板书试试：</span>
            {SAMPLES.map((sample) => (
              <button
                key={sample.url}
                type="button"
                className="sample-chip"
                disabled={busy !== null || sampleLoading !== ""}
                onClick={() => void loadSample(sample)}
              >
                {sampleLoading === sample.url ? "上传中…" : sample.label}
              </button>
            ))}
          </div>

          <h3>
            知识点卡片 <span className="count">{cards.length}</span>
            <span className="hint">识别错了？点「编辑」人工修正</span>
          </h3>
          {sources.length > 0 && (
            <SourceStrip
              className="source-strip-cards"
              title="来源文件"
              hint="点击查看"
              sources={sources}
              limit={CARD_SOURCE_LIMIT}
              showCaption={false}
              onOpen={setLightbox}
            />
          )}
          <ul className={`cards ${editingId !== null ? "cards-editing" : ""}`}>
            {cards.map((card) => (
              <li
                key={card.id}
                ref={editingId === card.id ? editorRef : undefined}
                className={`card ${editingId === card.id ? "card-editing" : ""}`}
              >
                {editingId === card.id ? (
                  <CardEditor
                    card={card}
                    onCancel={() => setEditingId(null)}
                    onSaved={handleCardSaved}
                  />
                ) : (
                  <>
                    <div className="card-head">
                      <div className="card-title">{card.title}</div>
                      <button
                        className="btn btn-sm btn-edit"
                        onClick={() => setEditingId(card.id)}
                        aria-label={`编辑卡片 ${card.title}`}
                      >
                        编辑
                      </button>
                    </div>
                    <div className="card-summary">{card.summary}</div>
                    {card.formula && (
                      <div className="card-formula">
                        <MathText text={`$${card.formula}$`} />
                      </div>
                    )}
                    {card.body && (
                      <details className="card-body">
                        <summary>教材原文</summary>
                        <p>{card.body}</p>
                      </details>
                    )}
                    {editedIds.includes(card.id) && (
                      <span className="tag tag-edited">已人工修正</span>
                    )}
                  </>
                )}
              </li>
            ))}
            {cards.length === 0 && (
              <li className="empty">还没有卡片，先上传一张板书</li>
            )}
          </ul>
        </section>

        <section className="panel panel-board">
          <h2>2 · Agent 协同看板</h2>
          {run && stages.length > 0 && (
            <RunProgress
              run={run}
              stages={stages}
              board={board}
              timeoutSec={timeoutSecOf(run.kind)}
            />
          )}
          <AgentBoard board={board} />
          <div className="log" ref={logRef}>
            {logs.map((item) => (
              <div key={item.seq} className={`log-line log-${item.phase}`}>
                <span className="log-agent">
                  {AGENT_LABEL[item.agent] ?? item.agent}
                </span>
                <span className="log-msg">{item.message || item.phase}</span>
              </div>
            ))}
            {logs.length === 0 && (
              <div className="log-empty">
                <p>上传一页纸后，这里会依次记录七个 Agent 的处理过程。</p>
                <p className="log-empty-sub">
                  命题与质检最多来回 3 轮（首轮 + 2 次重试），直到题目答案能被验算通过。
                </p>
              </div>
            )}
          </div>
        </section>

        <section className="panel panel-generate">
          <h2>3 · 一句话出题</h2>
          <div className="gen-row">
            <input
              className="text-input"
              value={requirement}
              disabled={busy !== null}
              onChange={(event) => setRequirement(event.target.value)}
              placeholder="用一句话描述你要的题"
            />
            <input
              className="num-input"
              type="number"
              min={MIN_QUESTIONS}
              max={MAX_QUESTIONS}
              value={count}
              disabled={busy !== null}
              onChange={(event) => setCount(clampCount(Number(event.target.value)))}
            />
            <button
              className="btn btn-primary"
              data-tour="generate"
              disabled={busy !== null || !requirement.trim()}
              onClick={() => void generate()}
            >
              {busy === "generate" ? "生成中…" : "生成练习卷"}
            </button>
          </div>

          <div className="questions" data-tour="questions">
            {questions.length > 0 && (
              <div className="verify-summary">
                <span className="vs-item">{questions.length} 题</span>
                <span className="vs-sep">·</span>
                <span className="vs-item vs-ok">
                  {questions.every((question) => question.verified)
                    ? "全部通过符号验算"
                    : `${questions.filter((question) => question.verified).length} 题通过验算`}
                </span>
                {verifyRejects > 0 && (
                  <>
                    <span className="vs-sep">·</span>
                    <span className="vs-item vs-warn">
                      驳回 {verifyRejects} 轮后重生成
                    </span>
                  </>
                )}
                <span className="vs-note">
                  验算由 sympy 代入完成，未通过的题不会出现在这里
                </span>
              </div>
            )}
            {questions.map((question, index) => {
              const result = results[question.id];
              const review = reviews[question.id];
              const reviewing = review?.status === "running";
              // 流式追加的草稿题还没有库 id（负数占位），先锁住作答与判分
              const ready = question.id > 0;
              return (
                <article
                  key={question.id}
                  className="question"
                  style={{
                    animationDelay: `${Math.min(
                      index * QUESTION_STAGGER_MS,
                      QUESTION_STAGGER_MAX_MS
                    )}ms`,
                  }}
                >
                  <div className="q-tags">
                    <span className="tag">{question.difficulty || "难度待定"}</span>
                    {ready ? (
                      <span
                        className={`tag ${question.verified ? "tag-ok" : "tag-warn"}`}
                      >
                        {question.verified ? "✓ 已验算" : "! 未验算"}
                      </span>
                    ) : (
                      <span className="tag tag-pending">待验算</span>
                    )}
                    <span className="q-id">{ready ? `#${question.id}` : "草稿"}</span>
                    {!question.verified && question.verify_log && (
                      <span className="q-verify">{question.verify_log}</span>
                    )}
                  </div>

                  <div className="q-stem">
                    <MathText text={question.stem_latex} />
                  </div>

                  {question.qtype === "choice" ? (
                    <div className="options">
                      {question.options.map((option, index) => {
                        const letter = LETTERS[index] ?? "?";
                        const active = answers[question.id] === letter;
                        return (
                          <label
                            key={index}
                            className={`option ${active ? "option-active" : ""}`}
                          >
                            <input
                              type="radio"
                              name={`q-${question.id}`}
                              checked={active}
                              disabled={busy !== null || !ready}
                              onChange={() =>
                                setAnswers((prev) => ({
                                  ...prev,
                                  [question.id]: letter,
                                }))
                              }
                            />
                            <span>
                              {letter}. {option}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  ) : (
                    <input
                      className="text-input"
                      placeholder="填写答案"
                      value={answers[question.id] ?? ""}
                      disabled={busy !== null || !ready}
                      onChange={(event) =>
                        setAnswers((prev) => ({
                          ...prev,
                          [question.id]: event.target.value,
                        }))
                      }
                    />
                  )}

                  <div className="q-actions">
                    <button
                      className="btn btn-sm"
                      disabled={
                        busy !== null ||
                        reviewing ||
                        !ready ||
                        !(answers[question.id] ?? "").trim()
                      }
                      onClick={() => void grade(question.id)}
                    >
                      {busy === "grade" ? "判分中…" : "提交判分"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-review"
                      data-tour={index === 0 ? "review" : undefined}
                      disabled={busy !== null || reviewing || !ready}
                      title={
                        ready
                          ? "让 review Agent 逐点批改这道题"
                          : "题目还在验算中，稍等一下"
                      }
                      onClick={() => void reviewQuestion(question.id)}
                    >
                      {reviewing ? "批改中…" : "AI 批改"}
                    </button>
                    {result && (
                      <span
                        className={`verdict ${result.correct ? "verdict-ok" : "verdict-bad"}`}
                      >
                        {result.correct ? "✓ 正确" : "✗ 错误"}
                      </span>
                    )}
                  </div>

                  {ready && review && (
                    <ReviewPanel
                      state={review}
                      onRetry={() => void reviewQuestion(question.id)}
                    />
                  )}

                  {result && !result.correct && (
                    <div className="solution">
                      <strong>参考解答：</strong>
                      <MathText text={question.solution} />
                    </div>
                  )}
                </article>
              );
            })}
            {questions.length === 0 && (
              <div className="empty">还没有题目，先点「生成练习卷」</div>
            )}
          </div>
        </section>

        <section className="panel panel-explain">
          <h2>4 · 错题讲解</h2>
          {explain ? (
            <>
              <h3 className="explain-title">{explain.data.title}</h3>
              <FunctionSlider key={explain.qid} explain={explain.data} />
              <p className="explain-script">{explain.data.script}</p>
            </>
          ) : (
            <div className="empty">答错一道题，这里会出现可拖动的讲解图</div>
          )}

          <div className="plot-sandbox">
            <div className="plot-head">
              <span className="plot-label">函数图像</span>
              <input
                className="text-input plot-input"
                value={plotExpression}
                spellCheck={false}
                onChange={(event) => setPlotExpression(event.target.value)}
                placeholder="例如 sin(x)/x 或 x^2-4x+3"
                aria-label="函数表达式"
              />
            </div>
            <FunctionPlot expression={plotExpression} />
            <p className="plot-hint">
              支持 <code>+ - * / ^</code>、<code>sin cos tan sqrt abs ln log exp</code>、常量{" "}
              <code>pi</code> <code>e</code>，可省略乘号（<code>2x</code>）。
            </p>
          </div>
        </section>

        <section className="panel panel-ask">
          <h2>5 · 追问（带出处的检索）</h2>
          <div className="gen-row">
            <input
              className="text-input"
              value={askQuery}
              disabled={busy !== null}
              onChange={(event) => setAskQuery(event.target.value)}
              placeholder="例如：为什么这里要配方？"
            />
            <button
              className="btn btn-primary"
              disabled={busy !== null || !askQuery.trim()}
              onClick={() => void ask()}
            >
              {busy === "ask" ? "检索中…" : "提问"}
            </button>
          </div>

          {askAnswer && (
            <div className="answer">
              <p>
                <MathText text={askAnswer.answer} />
              </p>
              <div className="citations">
                出处：
                {askAnswer.cards.map((card) => (
                  <span key={card.id} className="cite">
                    {card.title}
                  </span>
                ))}
                {askAnswer.cards.length === 0 && (
                  <span className="cite cite-none">无匹配材料</span>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="panel panel-graph" data-tour="graph">
          <div className="panel-head">
            <h2>6 · 知识谱系</h2>
            <button
              type="button"
              className="btn btn-sm"
              disabled={graphLoading}
              onClick={() => void loadKnowledge()}
            >
              {graphLoading ? "刷新中…" : "刷新"}
            </button>
          </div>
          <div className="graph-layout">
            <KnowledgeGraph graph={graph} loading={graphLoading} error={graphError} />
            <aside className="graph-side">
              <h3>
                最常被问 Top {TOP_ASKED}
                <span className="hint">来自 /api/stats</span>
              </h3>
              {stats && stats.top_asked.length > 0 ? (
                <ol className="top-asked">
                  {stats.top_asked.slice(0, TOP_ASKED).map((item, index) => (
                    <li key={item.id}>
                      <span className="rank">{index + 1}</span>
                      <span className="top-title" title={item.title}>
                        {item.title}
                      </span>
                      <span className="top-counts">
                        问 {item.ask_count} · 题 {item.question_count}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="empty">还没有提问记录，去第 5 栏追问一次。</div>
              )}
            </aside>
          </div>

          <div className="source-gallery">
            <SourceStrip
              title="知识库图册"
              hint={`最近 ${GALLERY_SOURCE_LIMIT} 个来源文件 · 左右滚动 · 点击查看`}
              sources={sources}
              limit={GALLERY_SOURCE_LIMIT}
              emptyText="还没有归档的来源文件"
              error={sourcesError}
              onOpen={setLightbox}
            />
          </div>
        </section>
      </main>

      {/* 拍照 / 看原图弹层打开时引导整体暂停（不抢按键），关掉弹层后同一步恢复 */}
      <TourGuide
        open={tourOpen}
        paused={cameraOpen || lightbox !== null}
        onClose={closeTour}
      />

      {cameraOpen && (
        <CameraCapture
          onClose={() => setCameraOpen(false)}
          onCapture={(file) => {
            setCameraOpen(false);
            void ingest(file);
          }}
        />
      )}

      {lightbox && (
        <SourceLightbox source={lightbox} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}
