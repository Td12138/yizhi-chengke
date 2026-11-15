import katex from "katex";

/**
 * 行内公式渲染：把 `$...$` 片段交给 KaTeX，其余按纯文本。
 * 渲染失败不抛错，退化为原文，保证演示不中断。
 */
export default function MathText({ text }: { text: string }) {
  const parts = String(text ?? "").split(/(\$[^$]*\$)/g);
  return (
    <span className="math-text">
      {parts.map((part, index) => {
        if (part.startsWith("$") && part.endsWith("$") && part.length > 2) {
          let html: string;
          try {
            html = katex.renderToString(part.slice(1, -1), {
              throwOnError: false,
              displayMode: false,
            });
          } catch {
            return <span key={index}>{part}</span>;
          }
          return (
            <span
              key={index}
              dangerouslySetInnerHTML={{ __html: html }}
            />
          );
        }
        return <span key={index}>{part}</span>;
      })}
    </span>
  );
}
