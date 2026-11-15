import { useEffect, useState } from "react";
import type { SourceItem } from "../types";

type Props = {
  source: SourceItem;
  /** thumb = 缩略图（固定比例），full = 弹层大图（保持原比例） */
  variant?: "thumb" | "full";
};

/** 只有这些后缀才走 <img>：PDF / DOCX / TXT / MD 塞进 img 只会得到破图 */
const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"];

function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** 文档徽标：PDF / DOCX / TXT / MD，认不出后缀时退回 DOC */
function docBadge(suffix: string): string {
  return suffix.replace(".", "").toUpperCase() || "DOC";
}

/**
 * 来源文件展示：
 *   - 图片：<img>；加载失败（404 / 文件被删）才换成「原图缺失」占位块
 *   - 文档：文件徽标 + 文件名，full 版另给「打开」链接（PDF 直接在弹层内预览）
 */
export default function SourceImage({ source, variant = "thumb" }: Props) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [source.image_url]);

  const suffix = suffixOf(String(source.display_name ?? ""));

  // 文档不是图片：不做 img 请求，直接给文件卡片，避免误报「原图缺失」
  if (!IMAGE_SUFFIXES.includes(suffix)) {
    const isPdf = suffix === ".pdf";
    const openLink = (
      <a
        className="source-doc-open"
        href={source.image_url}
        target="_blank"
        rel="noreferrer"
      >
        打开
      </a>
    );

    if (variant === "full") {
      return (
        <div className="source-doc source-doc-full">
          {isPdf && (
            // PDF 后端按 application/pdf 内联返回，Chromium 自带阅读器可直接预览
            <iframe
              className="source-doc-frame"
              src={source.image_url}
              title={source.display_name}
            />
          )}
          <div className="source-doc-head">
            <span className="file-badge" aria-hidden="true">
              {docBadge(suffix)}
            </span>
            <span className="file-name" title={source.display_name}>
              {source.display_name}
            </span>
            {openLink}
          </div>
        </div>
      );
    }

    return (
      <span className="source-doc" title={source.display_name}>
        <span className="file-badge" aria-hidden="true">
          {docBadge(suffix)}
        </span>
        <span className="source-doc-name">{source.display_name}</span>
      </span>
    );
  }

  if (failed) {
    return (
      <span
        className={`source-missing ${
          variant === "full" ? "source-missing-full" : ""
        }`}
      >
        原图缺失
      </span>
    );
  }

  return (
    <img
      className={variant === "full" ? "source-full" : "source-img"}
      src={source.image_url}
      alt={source.display_name}
      loading={variant === "thumb" ? "lazy" : "eager"}
      onError={() => setFailed(true)}
    />
  );
}
