import { useEffect, useRef } from "react";
import MathText, { MathBlock } from "./MathText";
import type { ReviewFeed, ReviewVerdict } from "../types";

/** 分数环半径 / 周长；SVG 尺寸由半径 + 描边反推，改一处即可 */
const RING_RADIUS = 26;
const RING_STROKE = 5;
const RING_SIZE = (RING_RADIUS + RING_STROKE) * 2;
const RING_CENTER = RING_SIZE / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;
/** 满分：review Agent 按 0–100 给分（apps/server/app/agents/review.py 的 SCORE_MAX） */
const RING_MAX = 100;

const VERDICT_LABEL: Record<ReviewVerdict, string> = {
  correct: "正确",
  partial: "部分正确",
  wrong: "错误",
};

/** 分数环填充比例：0 = 空环，1 = 满环 */
function ringFraction(score: number): number {
  if (!Number.isFinite(score)) return 0;
  return Math.min(1, Math.max(0, score / RING_MAX));
}

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
  const betterMethod = result?.better_method ?? null;
  const comparison = result?.method_comparison ?? "";

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
        <div className="review-result">
          <div className="review-summary">
            <div
              className={`review-score review-score-${tone}`}
              role="img"
              aria-label={`得分 ${formatScore(result.score)}`}
            >
              <svg
                className="score-ring"
                viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
                aria-hidden="true"
              >
                <circle
                  className="score-ring-track"
                  cx={RING_CENTER}
                  cy={RING_CENTER}
                  r={RING_RADIUS}
                  strokeWidth={RING_STROKE}
                />
                <circle
                  className="score-ring-fill"
                  cx={RING_CENTER}
                  cy={RING_CENTER}
                  r={RING_RADIUS}
                  strokeWidth={RING_STROKE}
                  strokeDasharray={RING_CIRCUMFERENCE}
                  strokeDashoffset={
                    RING_CIRCUMFERENCE * (1 - ringFraction(result.score))
                  }
                />
              </svg>
              <span className="score-value">{formatScore(result.score)}</span>
            </div>
            <span className="review-score-label">分</span>
          </div>

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
            <h4>更优解法</h4>
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
      )}
    </div>
  );
}
