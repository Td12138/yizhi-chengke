import type { Status } from "../types";

export const AGENTS = [
  { key: "recognize", label: "识别", role: "图像 → 版面块" },
  { key: "extract", label: "萃取", role: "版面块 → 知识点" },
  { key: "compose", label: "命题", role: "需求 → 变式题" },
  { key: "verify", label: "质检", role: "sympy 验算" },
  { key: "explain", label: "讲解", role: "错因 → 可视化" },
  { key: "retrieve", label: "检索", role: "卡片召回" },
] as const;

type BoardEntry = { phase: Status; message: string };

const PHASE_LABEL: Record<string, string> = {
  idle: "待命",
  queued: "排队",
  running: "运行中",
  delta: "输出中",
  done: "完成",
  rejected: "驳回",
  error: "失败",
};

export default function AgentBoard({
  board,
}: {
  board: Record<string, BoardEntry>;
}) {
  return (
    <div className="board">
      {AGENTS.map((agent) => {
        const entry = board[agent.key];
        const phase = entry?.phase ?? "idle";
        return (
          <div key={agent.key} className={`agent agent-${phase}`}>
            <div className="agent-head">
              <span className="agent-name">{agent.label}</span>
              <span className="agent-state">{PHASE_LABEL[phase]}</span>
            </div>
            <div className="agent-role">{agent.role}</div>
            <div className="agent-msg">{entry?.message || "—"}</div>
          </div>
        );
      })}
    </div>
  );
}
