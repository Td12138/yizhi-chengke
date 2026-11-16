import { useEffect, useState } from "react";
import { ApiError, fetchCardRevisions, updateCard } from "../api";
import type { Card, CardPatch, CardRevision } from "../types";
import MathText from "./MathText";

/** 标题为空时提交没有意义，先在前端拦住 */
const TITLE_MAX_CHARS = 120;

/** 修正历史里字段名 → 中文标签 */
const FIELD_LABEL: Record<string, string> = {
  title: "标题",
  summary: "摘要",
  formula: "公式",
  body: "正文",
};

/** 历史条目里旧/新值的最长展示字符数，超出截断（正文可能很长） */
const VALUE_PREVIEW_CHARS = 60;

function previewValue(value: string): string {
  const text = value.trim() || "（空）";
  return text.length > VALUE_PREVIEW_CHARS
    ? `${text.slice(0, VALUE_PREVIEW_CHARS)}…`
    : text;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

type Draft = {
  title: string;
  summary: string;
  formula: string;
  body: string;
};

function toDraft(card: Card): Draft {
  return {
    title: card.title ?? "",
    summary: card.summary ?? "",
    formula: card.formula ?? "",
    body: card.body ?? "",
  };
}

/** 只提交真正改过的字段，避免把空字段覆盖到后端。 */
function diffDraft(card: Card, draft: Draft): CardPatch {
  const patch: CardPatch = {};
  if (draft.title !== (card.title ?? "")) patch.title = draft.title;
  if (draft.summary !== (card.summary ?? "")) patch.summary = draft.summary;
  if (draft.formula !== (card.formula ?? "")) patch.formula = draft.formula;
  if (draft.body !== (card.body ?? "")) patch.body = draft.body;
  return patch;
}

export default function CardEditor({
  card,
  onCancel,
  onSaved,
}: {
  card: Card;
  onCancel: () => void;
  onSaved: (next: Card) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(card));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [revisions, setRevisions] = useState<CardRevision[]>([]);

  // 打开编辑器即拉修正历史；旧后端没有该路由时静默降级（不显示历史区块）
  useEffect(() => {
    let active = true;
    fetchCardRevisions(card.id)
      .then((items) => {
        if (active) setRevisions(items);
      })
      .catch(() => {
        if (active) setRevisions([]);
      });
    return () => {
      active = false;
    };
  }, [card.id]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const titleOk = draft.title.trim().length > 0;
  const dirty = Object.keys(diffDraft(card, draft)).length > 0;

  async function save() {
    if (saving) return;
    if (!titleOk) {
      setError("标题不能为空");
      return;
    }
    const patch = diffDraft(card, draft);
    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    setSaving(true);
    setError("");
    try {
      const next = await updateCard(card.id, patch);
      onSaved(next);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // 404 有两种来源：卡片真的没了，或后端还没挂上 PATCH 路由
        setError(
          err.detail && err.detail !== "Not Found"
            ? `卡片 #${card.id} 已不存在（可能已被删除），请刷新列表`
            : `后端尚未提供 PATCH /api/cards/${card.id}（接口就绪后即可保存）`
        );
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="card-editor"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="editor-head">
        <strong>人工修正 #{card.id}</strong>
        <span className="editor-hint">改完即回写卡片库，重新出题会用到新内容</span>
      </div>

      <label className="field">
        <span>标题 *</span>
        <input
          className="editor-input"
          value={draft.title}
          maxLength={TITLE_MAX_CHARS}
          disabled={saving}
          onChange={(event) => set("title", event.target.value)}
        />
      </label>

      <label className="field">
        <span>摘要</span>
        <textarea
          className="editor-input"
          rows={2}
          value={draft.summary}
          disabled={saving}
          onChange={(event) => set("summary", event.target.value)}
        />
      </label>

      <label className="field">
        <span>公式（LaTeX，不加 $）</span>
        <input
          className="editor-input"
          value={draft.formula}
          placeholder="例如 y=ax^2+bx+c"
          disabled={saving}
          onChange={(event) => set("formula", event.target.value)}
        />
      </label>
      {draft.formula.trim() && (
        <div className="editor-preview">
          预览：<MathText text={`$${draft.formula}$`} />
        </div>
      )}

      <label className="field">
        <span>正文（教材原文 / 板书要点）</span>
        <textarea
          className="editor-input"
          rows={4}
          value={draft.body}
          disabled={saving}
          onChange={(event) => set("body", event.target.value)}
        />
      </label>

      {error && <div className="editor-error">{error}</div>}

      <div className="editor-actions">
        <button className="btn btn-primary btn-sm" type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存修正"}
        </button>
        <button
          className="btn btn-sm"
          type="button"
          disabled={saving}
          onClick={onCancel}
        >
          取消
        </button>
        <span className="editor-hint">
          {dirty ? "有未保存的改动" : "尚未修改"}
        </span>
      </div>

      {revisions.length > 0 && (
        <details className="editor-revisions">
          <summary>修正历史（{revisions.length} 条，改前值已留痕）</summary>
          <ul>
            {revisions.map((revision) => (
              <li key={revision.id}>
                <span className="rev-field">
                  {FIELD_LABEL[revision.field] ?? revision.field}
                </span>
                <span className="rev-time">{formatTime(revision.created_at)}</span>
                <span className="rev-old">{previewValue(revision.old_value)}</span>
                <span className="rev-arrow" aria-hidden="true">→</span>
                <span className="rev-new">{previewValue(revision.new_value)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </form>
  );
}
