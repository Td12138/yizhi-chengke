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
const FAILED_PHASES: Status[] = ["error", "rejected"];

type StageState = "done" | "active" | "failed" | "pending";

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
  return "pending";
}

const STATE_MARK: Record<StageState, string> = {
  done: "✓",
  active: "●",
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
  const percent = Math.round((doneCount / Math.max(1, states.length)) * 100);
  const current =
    stages[states.findIndex((state) => state === "active")]?.label ?? "";

  const title = run.active
    ? `${run.label}中…`
    : failed
      ? `${run.label}失败`
      : `${run.label}完成`;

  return (
    <div className={`run-progress ${failed ? "run-progress-failed" : ""}`}>
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
          className={`run-bar-fill ${failed ? "run-bar-fill-bad" : ""}`}
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
