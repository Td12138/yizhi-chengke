import { useState } from "react";
import { postJSON } from "../api";
import { describeError } from "../lib/normalize";
import type { Card } from "../types";

/** 与 App 的上传入口保持一致：拍题讲题只处理图片，不放开 PDF / DOCX */
const UPLOAD_ACCEPT = "image/*,.pdf,.docx";
/** 变式题固定要求：同知识点、1 道 */
const VARIANT_REQUIREMENT = "基于这道题生成 1 道同知识点变式题";
const VARIANT_COUNT = 1;

type SolveParams = {
  a?: number;
  b?: number;
  c?: number;
  x_min?: number;
  x_max?: number;
};

type SolveResult = {
  title?: string;
  script?: string;
  params?: SolveParams;
};

type Props = {
  /** 当前要讲的卡片（取卡片区第一张） */
  card: Card | null;
  /** App 的全局忙碌标记：上传识别期间禁用面板按钮 */
  busyKind: string | null;
  /** 复用 App 既有的 ingest 流程，不另写上传逻辑 */
  onUpload: (file: File) => void;
};

/** 把 params 摊成一行可读文本；缺字段直接跳过 */
function formatParams(params: SolveParams): string {
  const parts: string[] = [];
  for (const key of ["a", "b", "c"] as const) {
    const value = params[key];
    if (typeof value === "number") parts.push(`${key} = ${value}`);
  }
  if (typeof params.x_min === "number" && typeof params.x_max === "number") {
    parts.push(`x ∈ [${params.x_min}, ${params.x_max}]`);
  }
  return parts.join("，");
}

/**
 * 「拍题讲题」小面板：上传一道题 → 讲这道题 → 改一道。
 * 纯加法，不影响原有上传 / 出题流程；所有失败都在面板内显示原文。
 */
export default function SolvePanel({ card, busyKind, onUpload }: Props) {
  const [solving, setSolving] = useState(false);
  const [sendingVariant, setSendingVariant] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SolveResult | null>(null);
  /** 结果跟着卡片走：换卡片后旧讲解不再显示 */
  const [resultCardId, setResultCardId] = useState<number | null>(null);
  const [variantNote, setVariantNote] = useState("");

  const uploading = busyKind === "ingest";
  const locked = busyKind !== null || solving || sendingVariant;
  const shown = result && card && resultCardId === card.id ? result : null;

  async function solve(target: Card) {
    setSolving(true);
    setError("");
    setVariantNote("");
    try {
      const data = await postJSON<SolveResult>("/api/solve", {
        card_id: target.id,
      });
      setResult({
        title: data.title ?? "",
        script: data.script ?? "",
        params: data.params,
      });
      setResultCardId(target.id);
    } catch (err) {
      setResult(null);
      setResultCardId(null);
      setError(`讲题失败：${describeError(err)}`);
    } finally {
      setSolving(false);
    }
  }

  async function makeVariant(target: Card) {
    setSendingVariant(true);
    setError("");
    setVariantNote("");
    try {
      await postJSON<{ run_id: string }>("/api/generate", {
        card_ids: [target.id],
        requirement: VARIANT_REQUIREMENT,
        count: VARIANT_COUNT,
      });
      setVariantNote("变式题已提交，出题结果看右侧「练习卷」区域。");
    } catch (err) {
      setError(`变式题提交失败：${describeError(err)}`);
    } finally {
      setSendingVariant(false);
    }
  }

  const paramsText = shown?.params ? formatParams(shown.params) : "";

  return (
    <div className="solve-panel">
      <div className="solve-row">
        <label className={`btn btn-sm ${locked ? "btn-disabled" : ""}`}>
          {uploading ? "识别中…" : "选一张题目图片"}
          <input
            type="file"
            accept={UPLOAD_ACCEPT}
            hidden
            disabled={locked}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) {
                setError("");
                setResult(null);
                setResultCardId(null);
                setVariantNote("");
                onUpload(file);
              }
            }}
          />
        </label>
        <span className="solve-hint">
          {card
            ? `当前卡片：第 ${card.id} 张`
            : "还没有卡片，先上传一张题目图片"}
        </span>
      </div>

      {card && (
        <div className="solve-card">
          <strong className="solve-card-title">{card.title}</strong>
          <p className="solve-card-summary">{card.summary}</p>
        </div>
      )}

      {card && (
        <div className="solve-row">
          <button
            type="button"
            className="btn btn-sm"
            disabled={locked}
            onClick={() => void solve(card)}
          >
            {solving ? "讲题中…" : "讲这道题"}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={locked}
            onClick={() => void makeVariant(card)}
          >
            {sendingVariant ? "提交中…" : "改一道"}
          </button>
        </div>
      )}

      {shown && (
        <div className="solve-result" role="status">
          {shown.title && <strong className="solve-card-title">{shown.title}</strong>}
          {shown.script && <p className="solve-script">{shown.script}</p>}
          {paramsText && <p className="solve-params">{paramsText}</p>}
        </div>
      )}

      {variantNote && (
        <p className="solve-note" role="status">
          {variantNote}
        </p>
      )}

      {error && (
        <p className="solve-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
