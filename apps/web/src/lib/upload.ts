/**
 * 上传前校验：格式与大小在前端先拦一道，给出明确原因，不等后端 400/413。
 *
 * 常量必须与后端 `apps/server/app/config.py` 的 ALLOWED_SUFFIXES /
 * DOC_SUFFIXES / MAX_*_BYTES 对齐；只改一侧会误拦或漏拦。
 */

/** 与后端 MAX_UPLOAD_BYTES（12MB）对齐 */
const MB = 1024 * 1024;
const MAX_IMAGE_BYTES = 12 * MB;
/** 与后端 MAX_DOC_BYTES（20MB）对齐 */
const MAX_DOC_BYTES = 20 * MB;
const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp", ".bmp"];
const DOC_SUFFIXES = [".pdf", ".docx", ".txt", ".md"];
const MAX_IMAGE_MB = MAX_IMAGE_BYTES / MB;
const MAX_DOC_MB = MAX_DOC_BYTES / MB;
const UPLOAD_FORMATS_HINT = "图片或 PDF/DOCX/TXT/MD";

export type UploadCheck =
  | { ok: true; kind: "image" | "document" }
  | { ok: false; message: string };

/** 小写后缀（含点）；没有扩展名返回空串 */
export function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** 文档预览的徽标文字：PDF / DOCX / TXT / MD */
export function fileBadge(name: string): string {
  return suffixOf(name).replace(".", "").toUpperCase() || "DOC";
}

/** 提交前校验：返回可上传类型，或一句能直接展示的原因 */
export function checkUpload(file: File): UploadCheck {
  const suffix = suffixOf(file.name);
  const isImage = IMAGE_SUFFIXES.includes(suffix);
  const isDoc = DOC_SUFFIXES.includes(suffix);
  if (!isImage && !isDoc) {
    return {
      ok: false,
      message: suffix
        ? `不支持 ${suffix}，请上传${UPLOAD_FORMATS_HINT}`
        : `文件「${file.name}」没有扩展名，请上传${UPLOAD_FORMATS_HINT}`,
    };
  }
  if (isDoc && file.size > MAX_DOC_BYTES) {
    return {
      ok: false,
      message: `文件过大（${(file.size / MB).toFixed(1)}MB）：文档上限 ${MAX_DOC_MB}MB，请压缩后再试`,
    };
  }
  if (isImage && file.size > MAX_IMAGE_BYTES) {
    return {
      ok: false,
      message: `图片过大（${(file.size / MB).toFixed(1)}MB）：图片上限 ${MAX_IMAGE_MB}MB，请压缩后再试`,
    };
  }
  return { ok: true, kind: isDoc ? "document" : "image" };
}
