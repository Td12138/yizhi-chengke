import { useEffect, useRef } from "react";
import MathText, { MathBlock } from "./MathText";
import type { ReviewFeed, ReviewVerdict } from "../types";

const VERDICT_LABEL: Record<ReviewVerdict, string> = {
  correct: "正确",
  partial: "部分正确",
  wrong: "错误",
};

function formatScore(score: number): string {
  if (!Number.isFinite(score)) return "—";
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

/**
 * 单题 AI 批改面板：
 * 运行中只显示流式文本（逐字追加 + 闪烁光标），结束后换成结论视图；
 * 失败在面板内给可读原因与「重试」，不污染顶部错误横幅。
 */
export default function ReviewPanel({
  state,
  onRetry,
}: {
  state: ReviewFeed;
  onRetry: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // 面板出现 / 重试时滚进可视区，长卷里不用手动找
  useEffect(() => {
    if (state.status === "running") {
      ref.current?.scrollIntoView({ block: "nearest" });
    }
  }, [state.status]);

  const result = state.result;
  const verdict = result?.verdict;
  const tone = verdict ?? "partial";
  const steps = result?.steps ?? [];
  const firstError = result?.first_error ?? null;
  const studentMethod = result?.student_method ?? null;
  const betterMethod = result?.better_method ?? null;
  const comparison = result?.method_comparison ?? "";
  const hitCount = result?.points.filter((point) => point.hit).length ?? 0;
  const pointCount = result?.points.length ?? 0;
  // 一句话批语：要点命中 + 第一个出错步骤，代替原来独立的大号分数环
  const remark =
    state.status === "ok" && result
      ? pointCount > 0
        ? `要点命中 ${hitCount}/${pointCount}${
            firstError ? `，问题出在第 ${firstError.step} 步。` : "，步骤完整。"
          }`
        : result.suggestion
      : "";

  return (
    <div
      ref={ref}
      className={`review-panel review-panel-${state.status}`}
      aria-busy={state.status === "running"}
    >
      <div className="review-head">
        <span className="review-title">AI 批改</span>
        {state.status === "running" && <span className="review-status">批改中…</span>}
        {state.status === "ok" && result && (
          <span className={`review-badge review-badge-${tone}`}>
            {verdict ? VERDICT_LABEL[verdict] : "已批改"}
          </span>
        )}
        {state.status === "failed" && <span className="review-status-bad">批改失败</span>}
        {state.status === "ok" && result && (
          <span
            className={`review-stamp review-stamp-${tone}`}
            role="img"
            aria-label={`得分 ${formatScore(result.score)}`}
          >
            <span className="review-stamp-score">{formatScore(result.score)}</span>
            <span className="review-stamp-unit">分</span>
          </span>
        )}
      </div>

      {state.status === "running" && (
        <div className="review-stream" role="status" aria-live="polite">
          {state.text || "正在阅读你的作答…"}
          <span className="review-cursor" aria-hidden="true">
            ▍
          </span>
        </div>
      )}

      {state.status === "failed" && (
        <div className="review-fail">
          <p className="review-error">{state.error || "批改未完成，请重试"}</p>
          {state.text && <p className="review-partial">{state.text}</p>}
          <button type="button" className="btn btn-sm btn-retry" onClick={onRetry}>
            重试
          </button>
        </div>
      )}

      {state.status === "ok" && result && (
        <>
          {remark && <p className="review-remark">{remark}</p>}
          <div className="review-result">
            <section className="review-section">
              <h4>命中要点</h4>
              {result.points.length > 0 ? (
                <ul className="review-points">
                  {result.points.map((point, index) => (
                    <li
                      key={index}
                      className={`review-point ${point.hit ? "review-point-hit" : "review-point-miss"}`}
                    >
                      <span className="review-point-mark" aria-hidden="true">
                        {point.hit ? "✓" : "✗"}
                      </span>
                      <MathText text={point.text} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="review-empty">没有逐点判定</p>
              )}
            </section>

            <section className="review-section review-steps">
              <h4>分步清单</h4>
              {steps.length > 0 ? (
                <ol className="review-step-list">
                  {steps.map((step, position) => {
                    const isError = firstError?.step === step.index;
                    return (
                      <li
                        key={`${step.index}-${position}`}
                        className={`review-step ${step.found ? "review-step-hit" : "review-step-miss"} ${isError ? "review-step-error" : ""}`}
                      >
                        <span className="review-step-mark" aria-hidden="true">
                          {step.found ? "✓" : "✗"}
                        </span>
                        <span className="review-step-index">{step.index}</span>
                        <span className="review-step-body">
                          <span className="review-step-expect">
                            <MathText text={step.expect} />
                          </span>
                          {step.note && (
                            <span className="review-step-note">
                              <MathText text={step.note} />
                            </span>
                          )}
                          {isError && firstError && (
                            <span className="review-step-error-note">
                              <MathText
                                text={`第 ${step.index} 步出错：${firstError.reason}`}
                              />
                            </span>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              ) : (
                <p className="review-empty">没有分步判定</p>
              )}
              {steps.length > 0 && !firstError && (
                <p className="review-step-complete">✓ 步骤完整</p>
              )}
              {firstError && !steps.some((step) => step.index === firstError.step) && (
                <p className="review-step-error-note">
                  <MathText
                    text={`第 ${firstError.step} 步出错：${firstError.reason}`}
                  />
                </p>
              )}
            </section>

            <section className="review-section review-method-wrap">
              <h4>解法点评</h4>
              {studentMethod && (
                <p
                  className={`review-student-method ${
                    studentMethod.valid
                      ? "review-student-method-ok"
                      : "review-student-method-bad"
                  }`}
                >
                  你的解法：{studentMethod.name}（
                  {studentMethod.valid ? "成立" : "不成立"}）
                  {studentMethod.note && (
                    <>
                      {" — "}
                      <MathText text={studentMethod.note} />
                    </>
                  )}
                </p>
              )}
              {betterMethod ? (
                <div className="review-method">
                  {comparison && (
                    <p className="review-method-compare">{comparison}</p>
                  )}
                  <div className="review-method-name">{betterMethod.name}</div>
                  {betterMethod.why && (
                    <p className="review-method-why">
                      <MathText text={betterMethod.why} />
                    </p>
                  )}
                  {betterMethod.steps.length > 0 && (
                    <ol className="review-method-steps">
                      {betterMethod.steps.map((item, index) => (
                        <li key={index}>
                          <MathText text={item} />
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              ) : (
                <p className="review-method-none">你的解法即为推荐解法</p>
              )}
            </section>

            <section className="review-section">
              <h4>扣分原因</h4>
              {result.issues.length > 0 ? (
                <ul className="review-issues">
                  {result.issues.map((issue, index) => (
                    <li key={index}>
                      <MathText text={issue} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="review-empty">没有扣分项</p>
              )}
            </section>

            {result.suggestion && (
              <section className="review-section review-suggestion">
                <h4>改进建议</h4>
                <p>
                  <MathText text={result.suggestion} />
                </p>
              </section>
            )}

            {result.reference && (
              <section className="review-section review-reference">
                <h4>标准答案</h4>
                <MathBlock latex={result.reference} />
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
