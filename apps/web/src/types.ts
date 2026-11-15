export type Status =
  | "queued"
  | "running"
  | "delta"
  | "done"
  | "rejected"
  | "error";

export interface AgentEvent {
  run_id: string;
  agent: string;
  phase: Status;
  message?: string;
  payload?: any;
  ts: number;
}

export interface Card {
  id: number;
  title: string;
  summary: string;
  subject: string;
  tags: string;
  formula: string;
  body: string;
}

/** 卡片人工修正：只提交改动字段，未提交的字段由后端保留 */
export type CardPatch = Partial<
  Pick<Card, "title" | "summary" | "formula" | "body">
>;

export interface Question {
  id: number;
  stem_latex: string;
  qtype: "fill" | "choice";
  options: string[];
  answer: string;
  solution: string;
  difficulty: string;
  verified: number;
  verify_log: string;
}

/** AI 批改的逐点判定：hit = true 表示用户答到了这个要点 */
export interface ReviewPoint {
  hit: boolean;
  text: string;
}

/** 分步判定：index 从 1 开始，found = 这一步在作答里做到 */
export interface ReviewStep {
  index: number;
  expect: string;
  found: boolean;
  /** 该步的补充说明（可为空） */
  note: string;
}

/** 第一个出错的步骤：面板上整行高亮并在下方追加「第 N 步出错」 */
export interface ReviewFirstError {
  step: number;
  reason: string;
}

/** 更优解法：名称 + 为什么更好 + 编号步骤 */
export interface ReviewBetterMethod {
  name: string;
  why: string;
  steps: string[];
}

/** 批改结论的整体判定 */
export type ReviewVerdict = "correct" | "partial" | "wrong";

/** 编排器最终的批改结论（orchestrator/done 的 payload.result） */
export interface ReviewResult {
  score: number;
  verdict?: ReviewVerdict;
  points: ReviewPoint[];
  issues: string[];
  suggestion: string;
  /** 标准答案，LaTeX 原文 */
  reference: string;
  /** 分步清单（旧后端可能没有此字段，归一化后为空数组） */
  steps: ReviewStep[];
  first_error: ReviewFirstError | null;
  better_method: ReviewBetterMethod | null;
  /** 一句话结论：你的解法几步、推荐解法几步 */
  method_comparison: string;
}

/** 单题批改面板状态：running 时只有流式文本，ok 时带结构化结论 */
export interface ReviewFeed {
  status: "running" | "ok" | "failed";
  /** review/delta 的 chunk 逐条累加 */
  text: string;
  result?: ReviewResult;
  /** 失败原因，面板内直接展示并可重试 */
  error?: string;
}

export interface Explain {
  template: string;
  title: string;
  params: { a: number; b: number; c: number; x_min: number; x_max: number };
  script: string;
}

export interface AskAnswer {
  answer: string;
  citations: number[];
  cards: { id: number; title: string; summary: string }[];
}

/** 谱系图节点：一个知识点卡片 + 使用情况计数 */
export interface GraphNode {
  id: number;
  title: string;
  summary: string;
  subject: string;
  tags: string[];
  ask_count: number;
  question_count: number;
}

/** 谱系图边：两个知识点共同出现的关系，weight 越大关联越强 */
export interface GraphEdge {
  source: number;
  target: number;
  weight: number;
  tags: string[];
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  tags: string[];
}

/** 单个知识点的使用计数 */
export interface StatsItem {
  id: number;
  title: string;
  ask_count: number;
  question_count: number;
}

export interface StatsData {
  top_asked: StatsItem[];
}

/** 一张归档的原图：上传 / 拍照后按识别结果重命名保存 */
export interface SourceItem {
  id: number;
  display_name: string;
  created_at: number;
  card_count: number;
  image_url: string;
}

export interface SourcesData {
  sources: SourceItem[];
}
