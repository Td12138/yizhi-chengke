/**
 * 流式数据的结构归一：真实模型的输出缺字段、类型漂移时，界面仍要能渲染。
 * 纯函数、无 React 依赖，便于用 `node --test` 直接跑。
 */

import type {
  Card,
  Question,
  ReviewBetterMethod,
  ReviewFirstError,
  ReviewResult,
  ReviewStep,
  ReviewVerdict,
} from "../types";

/** 生成题数范围：与后端 /api/generate 的约束一致 */
export const MIN_QUESTIONS = 1;
export const MAX_QUESTIONS = 12;

export function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** 题数夹到 1–12：输入框清空（NaN）、手打 99 都不越界 */
export function clampCount(value: number): number {
  if (!Number.isFinite(value)) return MIN_QUESTIONS;
  return Math.min(MAX_QUESTIONS, Math.max(MIN_QUESTIONS, Math.round(value)));
}

/**
 * 出题流里的单题：delta 的 payload.question 可能只有部分字段，
 * 缺什么补什么，避免渲染阶段因空值崩掉。
 */
export function normalizeStreamedQuestion(raw: any, tempId: number): Question {
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
export function normalizeStreamedCard(raw: any, tempId: number): Card {
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
export function readReviewSteps(raw: any): ReviewStep[] {
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

export function readFirstError(raw: any): ReviewFirstError | null {
  if (!raw || typeof raw !== "object") return null;
  const step = Number(raw.step);
  if (!Number.isFinite(step) || step <= 0) return null;
  return {
    step: Math.trunc(step),
    reason: typeof raw.reason === "string" ? raw.reason : "",
  };
}

export function readBetterMethod(raw: any): ReviewBetterMethod | null {
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
export function readReviewResult(raw: any): ReviewResult | null {
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
