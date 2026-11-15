import { useEffect, useState } from "react";
import type { SourceItem } from "../types";

type Props = {
  source: SourceItem;
  /** thumb = 缩略图（固定比例），full = 弹层大图（保持原比例） */
  variant?: "thumb" | "full";
};

/**
 * 原图展示：加载失败（404 / 文件被删）时换成占位块并写明「原图缺失」，
 * 不让缩略图位置只剩一个破图标。
 */
export default function SourceImage({ source, variant = "thumb" }: Props) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [source.image_url]);

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
