import { Fragment } from "react";
import type { Status } from "../types";

export const AGENTS = [
  { key: "recognize", label: "识别", role: "图像 → 版面块" },
  { key: "extract", label: "萃取", role: "版面块 → 知识点" },
  { key: "compose", label: "命题", role: "需求 → 变式题" },
  { key: "verify", label: "质检", role: "sympy 验算" },
  { key: "explain", label: "讲解", role: "错因 → 可视化" },
  { key: "retrieve", label: "检索", role: "卡片召回" },
  { key: "review", label: "批改", role: "作答 → 逐点诊断" },
] as const;

/** 序号徽标补零位数：01 ~ 07 */
const AGENT_NO_PAD = 2;

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

/** 状态符号：投影时颜色之外还能靠形状区分，避免色弱/远距离看不清 */
const PHASE_MARK: Record<string, string> = {
  idle: "○",
  queued: "◔",
  running: "●",
  delta: "◐",
  done: "✓",
  rejected: "✕",
  error: "!",
};

/**
 * 七步流水线状态条：7 个节点一字排开，节点之间用箭头连接。
 * 节点只保留三行信息（工序号 + 名称 + 状态 / 职责或当前消息），
 * 空闲时不再画一大片写着「—」的监控格。
 */
export default function AgentBoard({
  board,
}: {
  board: Record<string, BoardEntry>;
}) {
  return (
    <div className="board">
      {AGENTS.map((agent, index) => {
        const entry = board[agent.key];
        const phase = entry?.phase ?? "idle";
        const label = PHASE_LABEL[phase] ?? phase;
        const detail = entry?.message?.trim() || agent.role;
        return (
          <Fragment key={agent.key}>
            {index > 0 && (
              <span className="board-link" aria-hidden="true">
                →
              </span>
            )}
            <div
              className={`agent agent-${phase}`}
              title={`${agent.label}：${label}${entry?.message ? ` · ${entry.message}` : ""}`}
            >
              <div className="agent-head">
                <span className="agent-title">
                  <span className="agent-no" aria-hidden="true">
                    {String(index + 1).padStart(AGENT_NO_PAD, "0")}
                  </span>
                  <span className="agent-name">{agent.label}</span>
                </span>
                <span className="agent-state">
                  <span className="agent-mark" aria-hidden="true">
                    {PHASE_MARK[phase] ?? "○"}
                  </span>
                  {label}
                </span>
              </div>
              <div className="agent-msg">{detail}</div>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}
