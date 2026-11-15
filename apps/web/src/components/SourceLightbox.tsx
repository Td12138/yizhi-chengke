import { useEffect } from "react";
import type { SourceItem } from "../types";
import SourceImage from "./SourceImage";

type Props = {
  source: SourceItem;
  onClose: () => void;
};

/** created_at 可能是秒级或毫秒级时间戳，用这个阈值区分 */
const MS_THRESHOLD = 1e12;

function formatDate(value: number): string {
  const ms = value > MS_THRESHOLD ? value : value * 1000;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** 原图大图弹层：复用拍照弹层的遮罩与卡片样式 */
export default function SourceLightbox({ source, onClose }: Props) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="camera-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`原图：${source.display_name}`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="camera-modal source-modal">
        <header className="camera-head">
          <h3>{source.display_name}</h3>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            关闭
          </button>
        </header>

        <SourceImage source={source} variant="full" />

        <div className="source-meta">
          <span className="tag">{formatDate(source.created_at)}</span>
          <span className="tag">{source.card_count} 个知识点</span>
          <span className="hint">原图按识别结果重命名归档</span>
        </div>
      </div>
    </div>
  );
}
