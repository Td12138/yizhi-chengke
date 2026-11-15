import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import AgentBoard, { AGENTS } from "./components/AgentBoard";
import FunctionSlider from "./components/FunctionSlider";
import MathText from "./components/MathText";
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

type Board = Record<string, { phase: Status; message: string }>;
type LogItem = { seq: number; agent: string; phase: Status; message: string };

export default function App() {
  const [cards, setCards] = useState<Card[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [results, setResults] = useState<Record<number, { correct: boolean }>>({});
  const [explain, setExplain] = useState<{ qid: number; data: Explain } | null>(null);
  const [board, setBoard] = useState<Board>({});
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
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
  const seq = useRef(0);

  useEffect(() => {
    getJSON<{ cards: Card[] }>("/api/cards")
      .then((data) => setCards(data.cards))
      .catch(() => undefined);
    getJSON<{ vlm_provider: string; llm_provider: string }>("/api/health")
      .then(setHealth)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

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

  async function execute(
    kind: string,
    start: () => Promise<{ run_id: string }>,
    after?: (last: AgentEvent | null) => void | Promise<void>
  ) {
    if (busy) return;
    setBusy(kind);
    setError("");
    setBoard({});
    try {
      const { run_id } = await start();
      const last = await streamRun(run_id, handleEvent);
      if (last?.phase === "error") {
        setError(last.message ?? "运行失败");
      }
      if (after) await after(last);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const resultOf = (last: AgentEvent | null) => last?.payload?.result ?? null;

  async function ingest(file: File) {
    setPreview(URL.createObjectURL(file));
    await execute(
      "ingest",
      () => postFile<{ run_id: string }>("/api/ingest", file),
      async () => {
        const data = await getJSON<{ cards: Card[] }>("/api/cards");
        setCards(data.cards);
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
        if (!result?.questions) return;
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
        setResults((prev) => ({
          ...prev,
          [questionId]: { correct: Boolean(result?.correct) },
        }));
        if (result?.explain) {
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
        if (result?.answer) setAskAnswer(result as AskAnswer);
      }
    );
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) void ingest(file);
  }

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
          {busy && <span className="pill pill-busy">运行中 · {busy}</span>}
        </div>
      </header>

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
            <label className="btn">
              选择图片或拍照
              <input
                type="file"
                accept="image/*"
                capture="environment"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void ingest(file);
                }}
              />
            </label>
          </div>

          <h3>
            知识点卡片 <span className="count">{cards.length}</span>
          </h3>
          <ul className="cards">
            {cards.map((card) => (
              <li key={card.id} className="card">
                <div className="card-title">{card.title}</div>
                <div className="card-summary">{card.summary}</div>
                {card.formula && (
                  <div className="card-formula">
                    <MathText text={`$${card.formula}$`} />
                  </div>
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
          {error && <div className="error">{error}</div>}
        </section>

        <section className="panel panel-generate">
          <h2>3 · 一句话出题</h2>
          <div className="gen-row">
            <input
              className="text-input"
              value={requirement}
              onChange={(event) => setRequirement(event.target.value)}
              placeholder="用一句话描述你要的题"
            />
            <input
              className="num-input"
              type="number"
              min={1}
              max={12}
              value={count}
              onChange={(event) => setCount(Number(event.target.value))}
            />
            <button
              className="btn btn-primary"
              disabled={busy !== null}
              onClick={() => void generate()}
            >
              生成练习卷
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
                      {question.verified ? "已验算" : "未验算"}
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
                      提交判分
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
                      参考解答：<MathText text={question.solution} />
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
              onChange={(event) => setAskQuery(event.target.value)}
              placeholder="例如：为什么这里要配方？"
            />
            <button
              className="btn btn-primary"
              disabled={busy !== null || !askQuery.trim()}
              onClick={() => void ask()}
            >
              提问
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
