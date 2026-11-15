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
