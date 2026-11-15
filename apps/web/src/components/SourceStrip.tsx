import type { SourceItem } from "../types";
import SourceImage from "./SourceImage";

type Props = {
  sources: SourceItem[];
  title: string;
  hint?: string;
  /** 没有归档图片时的引导文案；不传则不显示该区块 */
  emptyText?: string;
  /** 接口失败时的提示，避免整块静默消失 */
  error?: string;
  onOpen: (source: SourceItem) => void;
  /** 只展示最近 N 张，按 created_at 倒序 */
  limit?: number;
  showCaption?: boolean;
  className?: string;
};

/** 横向可滚动的原图缩略带：卡片区与知识库图册共用 */
export default function SourceStrip({
  sources,
  title,
  hint,
  emptyText,
  error,
  onOpen,
  limit,
  showCaption = true,
  className,
}: Props) {
  const recent = [...sources]
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, limit ?? sources.length);

  return (
    <section className={`source-strip ${className ?? ""}`}>
      <div className="source-strip-head">
        <span>{title}</span>
        {hint && <span className="hint">{hint}</span>}
      </div>

      {error ? (
        <p className="source-strip-error">来源图片加载失败：{error}</p>
      ) : recent.length === 0 ? (
        emptyText && <p className="source-empty">{emptyText}</p>
      ) : (
        <div className="source-thumbs">
          {recent.map((source) => (
            <button
              key={source.id}
              type="button"
              className="source-thumb"
              title={source.display_name}
              onClick={() => onOpen(source)}
            >
              <SourceImage source={source} />
              {showCaption && (
                <span className="source-name">{source.display_name}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
