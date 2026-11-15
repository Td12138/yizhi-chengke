import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import AgentBoard, { AGENTS } from "./components/AgentBoard";
import CardEditor from "./components/CardEditor";
import FunctionSlider from "./components/FunctionSlider";
import MathText from "./components/MathText";
import RunProgress from "./components/RunProgress";
import type { Board, RunInfo, Stage } from "./components/RunProgress";
import { getJSON, postFile, postJSON, streamRun } from "./api";
import type {
  AgentEvent,
  AskAnswer,
  Card,
  Explain,
  Question,
  Status,
} from "./types";

const AGENT_LABEL: Record<string, string> = {
  ...Object.fromEntries(AGENTS.map((agent) => [agent.key, agent.label])),
  orchestrator: "编排",
};
const LETTERS = "ABCDEFGH";
const MAX_LOGS = 200;
/** 与 api.ts 的 RUN_TIMEOUT_MS 对齐，仅用于文案展示 */
const RUN_TIMEOUT_S = 120;
const NOTICE_MS = 4_000;

const ACTION_LABEL: Record<string, string> = {
  ingest: "上传识别",
  generate: "生成练习卷",
  grade: "判分讲解",
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
  ask: [
    { label: "检索卡片", agents: ["retrieve"] },
    { label: "组织回答", agents: ["orchestrator"] },
  ],
};

type LogItem = { seq: number; agent: string; phase: Status; message: string };
type RunError = { step: string; message: string; retry: boolean };

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export default function App() {
  const [cards, setCards] = useState<Card[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editedIds, setEditedIds] = useState<number[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [results, setResults] = useState<Record<number, { correct: boolean }>>({});
  const [explain, setExplain] = useState<{ qid: number; data: Explain } | null>(null);
  const [board, setBoard] = useState<Board>({});
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState<RunError | null>(null);
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState("");
  const [requirement, setRequirement] = useState("给我出 5 道这页的变式题，中等难度");
  const [count, setCount] = useState(5);
  const [askQuery, setAskQuery] = useState("");
  const [askAnswer, setAskAnswer] = useState<AskAnswer | null>(null);
  const [health, setHealth] = useState<{
    vlm_provider: string;
    llm_provider: string;
  } | null>(null);

  const logRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLLIElement>(null);
  const seq = useRef(0);
  const retryRef = useRef<(() => void) | null>(null);
  const noticeTimer = useRef<number | undefined>(undefined);
  const previewUrl = useRef("");

  useEffect(() => {
    getJSON<{ cards: Card[] }>("/api/cards")
      .then((data) => setCards(data.cards))
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
  function failRun(step: string, message: string) {
    setError({ step, message, retry: true });
    setRun((prev) => (prev ? { ...prev, failed: true } : prev));
  }

  function handleEvent(event: AgentEvent) {
    const message = event.message ?? "";
    setBoard((prev) => ({
      ...prev,
      [event.agent]: { phase: event.phase, message },
    }));
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
   */
  async function execute(
    kind: string,
    start: () => Promise<{ run_id: string }>,
    after?: (last: AgentEvent | null) => void | Promise<void>
  ) {
    if (busy) return;
    const label = ACTION_LABEL[kind] ?? kind;

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
      void execute(kind, start, after);
    };

    try {
      let runId: string;
      try {
        const started = await start();
        runId = started.run_id;
      } catch (err) {
        failRun(`${label} · 发起任务`, describeError(err));
        return;
      }

      const outcome = await streamRun(runId, handleEvent, {
        timeoutMs: RUN_TIMEOUT_S * 1000,
        onTick: (elapsedMs) =>
          setRun((prev) => (prev && prev.active ? { ...prev, elapsedMs } : prev)),
      });

      if (outcome.reason === "timeout") {
        failRun(
          `${label} · 等待结果`,
          `超过 ${RUN_TIMEOUT_S} 秒仍未收到结束事件，已停止等待。可重试；若反复超时请查看后端日志。`
        );
        return;
      }

      if (outcome.reason === "broken") {
        failRun(
          `${label} · 事件流`,
          outcome.eventCount === 0
            ? "没有收到任何事件（后端未响应或任务已结束），请重试。"
            : `事件流提前中断：已收到 ${outcome.eventCount} 条事件但缺少结束标记，结果可能不完整，可重试。`
        );
        return;
      }

      if (outcome.reason === "error" || outcome.last?.phase === "error") {
        const who = outcome.last
          ? AGENT_LABEL[outcome.last.agent] ?? outcome.last.agent
          : "未知环节";
        failRun(
          `${label} · ${who}阶段`,
          outcome.last?.message || "任务以错误状态结束"
        );
        return;
      }

      if (after) {
        try {
          await after(outcome.last);
        } catch (err) {
          failRun(`${label} · 加载结果`, describeError(err));
        }
      }
    } finally {
      setBusy(null);
      setRun((prev) => (prev ? { ...prev, active: false } : prev));
    }
  }

  const resultOf = (last: AgentEvent | null) => last?.payload?.result ?? null;

  async function ingest(file: File) {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    const url = URL.createObjectURL(file);
    previewUrl.current = url;
    setPreview(url);

    await execute(
      "ingest",
      () => postFile<{ run_id: string }>("/api/ingest", file),
      async () => {
        const data = await getJSON<{ cards: Card[] }>("/api/cards");
        setCards(data.cards);
        setEditingId(null);
        flash(`已入库，当前共 ${data.cards.length} 张卡片`);
      }
    );
  }

  async function generate() {
    await execute(
      "generate",
      () =>
        postJSON<{ run_id: string }>("/api/generate", {
          card_ids: cards.map((card) => card.id),
          requirement,
          count,
        }),
      (last) => {
        const result = resultOf(last);
        if (!result?.questions?.length) {
          throw new Error("返回结果里没有题目（可能全部被质检驳回），请调整需求后重试");
        }
        setQuestions(result.questions);
        setAnswers({});
        setResults({});
        setExplain(null);
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

  async function ask() {
    await execute(
      "ask",
      () => postJSON<{ run_id: string }>("/api/ask", { query: askQuery }),
      (last) => {
        const result = resultOf(last);
        if (!result?.answer) throw new Error("回答结果为空");
        setAskAnswer(result as AskAnswer);
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
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
            {preview ? (
              <img src={preview} alt="上传预览" />
            ) : (
              <p className="dropzone-hint">把板书 / 试卷 / 讲义拖进来</p>
            )}
            {busy === "ingest" && (
              <p className="dropzone-status" role="status">
                上传并识别中… 看板第 2 栏显示进度
              </p>
            )}
            <label className={`btn ${busy ? "btn-disabled" : ""}`}>
              {busy === "ingest" ? "识别中…" : "选择图片或拍照"}
              <input
                type="file"
                accept="image/*"
                capture="environment"
                hidden
                disabled={busy !== null}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void ingest(file);
                }}
              />
            </label>
          </div>

          <h3>
            知识点卡片 <span className="count">{cards.length}</span>
            <span className="hint">识别错了？点「编辑」人工修正</span>
          </h3>
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
              timeoutSec={RUN_TIMEOUT_S}
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
            {logs.length === 0 && <div className="log-empty">等待运行……</div>}
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
              min={1}
              max={12}
              value={count}
              disabled={busy !== null}
              onChange={(event) => setCount(Number(event.target.value))}
            />
            <button
              className="btn btn-primary"
              disabled={busy !== null}
              onClick={() => void generate()}
            >
              {busy === "generate" ? "生成中…" : "生成练习卷"}
            </button>
          </div>

          <div className="questions">
            {questions.map((question) => {
              const result = results[question.id];
              return (
                <article key={question.id} className="question">
                  <div className="q-tags">
                    <span className="tag">{question.difficulty}</span>
                    <span
                      className={`tag ${question.verified ? "tag-ok" : "tag-warn"}`}
                    >
                      {question.verified ? "✓ 已验算" : "! 未验算"}
                    </span>
                    <span className="q-id">#{question.id}</span>
                    {question.verify_log && (
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
                              disabled={busy !== null}
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
                      disabled={busy !== null}
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
                      disabled={busy !== null || !(answers[question.id] ?? "").trim()}
                      onClick={() => void grade(question.id)}
                    >
                      {busy === "grade" ? "判分中…" : "提交判分"}
                    </button>
                    {result && (
                      <span
                        className={`verdict ${result.correct ? "verdict-ok" : "verdict-bad"}`}
                      >
                        {result.correct ? "✓ 正确" : "✗ 错误"}
                      </span>
                    )}
                  </div>

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
              <FunctionSlider explain={explain.data} />
              <p className="explain-script">{explain.data.script}</p>
            </>
          ) : (
            <div className="empty">答错一道题，这里会出现可拖动的讲解图</div>
          )}
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
      </main>
    </div>
  );
}
