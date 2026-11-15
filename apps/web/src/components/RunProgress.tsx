import type { Status } from "../types";

export type Stage = { label: string; agents: readonly string[] };

export type RunInfo = {
  kind: string;
  label: string;
  /** 是否仍在运行（决定动画与计时器） */
  active: boolean;
  /** 本次运行是否以失败/中断结束 */
  failed: boolean;
  /** 已等待时长（毫秒） */
  elapsedMs: number;
};

export type Board = Record<string, { phase: Status; message: string }>;

const ACTIVE_PHASES: Status[] = ["queued", "running", "delta"];
/** 真失败：只有 error 算失败；rejected 是质检驳回，随后会自动重跑 */
const FAILED_PHASES: Status[] = ["error"];
/** 驳回：单独黄灯提示，不计入失败，避免进度条先红一轮 */
const WARN_PHASES: Status[] = ["rejected"];

type StageState = "done" | "active" | "warn" | "failed" | "pending";

function stageState(stage: Stage, board: Board): StageState {
  // 没有归属 Agent 的环节（如「上传」）随运行开始即算完成
  if (stage.agents.length === 0) return "done";
  const phases = stage.agents.map((agent) => board[agent]?.phase);
  if (phases.some((phase) => phase && FAILED_PHASES.includes(phase))) {
    return "failed";
  }
  if (phases.every((phase) => phase === "done")) return "done";
  if (phases.some((phase) => phase && ACTIVE_PHASES.includes(phase))) {
    return "active";
  }
  if (phases.some((phase) => phase && WARN_PHASES.includes(phase))) {
    return "warn";
  }
  return "pending";
}

const STATE_MARK: Record<StageState, string> = {
  done: "✓",
  active: "●",
  warn: "!",
  failed: "✕",
  pending: "○",
};

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, "0")}s`;
}

export default function RunProgress({
  run,
  stages,
  board,
  timeoutSec,
}: {
  run: RunInfo;
  stages: Stage[];
  board: Board;
  timeoutSec: number;
}) {
  const states = stages.map((stage) => stageState(stage, board));
  const doneCount = states.filter((state) => state === "done").length;
  const failed = run.failed || states.includes("failed");
  // 只有「被驳回」没有「真失败」时走黄灯：整条进度不标红
  const warned = !failed && states.includes("warn");
  const percent = Math.round((doneCount / Math.max(1, states.length)) * 100);
  const current =
    stages[states.findIndex((state) => state === "active")]?.label ?? "";

  const title = run.active
    ? `${run.label}中…`
    : failed
      ? `${run.label}失败`
      : `${run.label}完成`;

  const progressClass = failed
    ? "run-progress-failed"
    : warned
      ? "run-progress-warn"
      : "";
  const fillClass = failed
    ? "run-bar-fill-bad"
    : warned
      ? "run-bar-fill-warn"
      : "";

  return (
    <div className={`run-progress ${progressClass}`}>
      <div className="run-progress-head">
        <span className="run-title">
          {title}
          {run.active && current && <span className="run-current">当前：{current}</span>}
        </span>
        <span className="run-elapsed">
          已等待 {formatElapsed(run.elapsedMs)}
          <span className="run-limit"> / 上限 {timeoutSec}s</span>
        </span>
      </div>

      <div
        className="run-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div
          className={`run-bar-fill ${fillClass}`}
          style={{ width: `${Math.max(percent, run.active ? 6 : 0)}%` }}
        />
      </div>

      <ul className="run-stages">
        {stages.map((stage, index) => (
          <li key={stage.label} className={`run-stage run-stage-${states[index]}`}>
            <span aria-hidden="true">{STATE_MARK[states[index]]}</span> {stage.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
