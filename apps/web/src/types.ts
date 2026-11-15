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
