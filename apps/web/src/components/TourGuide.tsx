import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * 首次打开的新手引导。
 *
 * 触发：
 *   1. 地址带 ?tour=1（桌面版首次启动由 Electron 追加，强制展示一次）
 *   2. localStorage 里没有 yz.tour.done（首次访问）
 * 关闭（「跳过」或走完最后一步）后写入 yz.tour.done=1，刷新不再出现。
 *
 * 检测目标元素用 data-tour 标记（见 App.tsx），比类名 / 文案稳定；
 * 目标不存在时（例如还没有题目时的「AI 批改」）自动跳到下一步，不卡住。
 *
 * 与弹层的约定：拍照 / 看原图弹层打开时本组件整体暂停（不渲染、不监听按键），
 * 弹层关闭后原地恢复同一步，因此不会和弹层抢 Esc，也不会把引导状态弄丢。
 */

/** 走完或跳过后写入的标记键 */
export const TOUR_DONE_KEY = "yz.tour.done";
/** 强制展示的查询参数（桌面版首次启动追加 ?tour=1） */
export const TOUR_QUERY_KEY = "tour";
const TOUR_QUERY_VALUE = "1";

/** 高亮框相对目标外扩的像素 */
const HOLE_PADDING = 6;
/** 气泡最大宽度；窄屏按视口收窄 */
const BUBBLE_MAX_WIDTH = 320;
/** 气泡与高亮框的间距 */
const BUBBLE_GAP = 12;
/** 气泡与视口边缘的最小留白 */
const VIEWPORT_MARGIN = 12;
/** 目标滚动进视野时预留的边距 */
const SCROLL_MARGIN = 24;

type TourStep = {
  key: string;
  /** 首选目标选择器 */
  target: string;
  /** 首选目标不存在时的备用目标 */
  fallback?: string;
  /** 主文案（来自产品需求，不要随手改） */
  text: string;
  /** 用备用目标时追加的一句说明 */
  fallbackNote?: string;
};

const STEPS: TourStep[] = [
  {
    key: "upload",
    target: '[data-tour="upload"]',
    fallback: ".dropzone",
    text: "拍一页纸，或者点下面的示例板书直接试",
  },
  {
    key: "generate",
    target: '[data-tour="generate"]',
    text: "用一句话描述你要的题，AI 出题后会逐题验算",
  },
  {
    key: "review",
    target: '[data-tour="review"]',
    fallback: '[data-tour="questions"]',
    text: "答错也能批改：指出错在第几步，并给出更优解法",
    fallbackNote:
      "现在还没有题目：先在第 3 栏生成一份练习卷，这里就会出现「AI 批改」。",
  },
  {
    key: "graph",
    target: '[data-tour="graph"]',
    fallback: ".panel-graph",
    text: "每个知识点被问过多少次，都记在这里",
  },
];

function readTourDone(): boolean {
  try {
    return window.localStorage.getItem(TOUR_DONE_KEY) === "1";
  } catch {
    // 隐私模式 / 存储被禁用时按「没看过」处理，不影响引导本身
    return false;
  }
}

/** 关闭引导后调用：跳过和走完都算完成 */
export function markTourDone() {
  try {
    window.localStorage.setItem(TOUR_DONE_KEY, "1");
  } catch {
    // 写不进去就算了，不阻断页面
  }
}

/** 是否应该自动打开引导 */
export function shouldAutoStartTour(): boolean {
  let forced = false;
  try {
    forced =
      new URLSearchParams(window.location.search).get(TOUR_QUERY_KEY) ===
      TOUR_QUERY_VALUE;
  } catch {
    forced = false;
  }
  return forced || !readTourDone();
}

/** 只认可见元素：宽高为 0（display:none 等）视为不存在 */
function queryVisible(selector: string): HTMLElement | null {
  const element = document.querySelector<HTMLElement>(selector);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 ? element : null;
}

function resolveStep(step: TourStep): { element: HTMLElement; fallback: boolean } | null {
  const primary = queryVisible(step.target);
  if (primary) return { element: primary, fallback: false };
  if (step.fallback) {
    const alternate = queryVisible(step.fallback);
    if (alternate) return { element: alternate, fallback: true };
  }
  return null;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

type Box = { top: number; left: number; width: number; height: number };
type Geometry = { hole: Box; bubble: { top: number; left: number; width: number } };

function sameBox(a: Box, b: Box): boolean {
  return (
    Math.abs(a.top - b.top) < 0.5 &&
    Math.abs(a.left - b.left) < 0.5 &&
    Math.abs(a.width - b.width) < 0.5 &&
    Math.abs(a.height - b.height) < 0.5
  );
}

function sameGeometry(a: Geometry | null, b: Geometry): boolean {
  return (
    !!a &&
    sameBox(a.hole, b.hole) &&
    Math.abs(a.bubble.top - b.bubble.top) < 0.5 &&
    Math.abs(a.bubble.left - b.bubble.left) < 0.5 &&
    Math.abs(a.bubble.width - b.bubble.width) < 0.5
  );
}

type Props = {
  open: boolean;
  /** 弹层打开时暂停：不渲染、不抢按键，关闭弹层后原步恢复 */
  paused?: boolean;
  onClose: () => void;
};

export default function TourGuide({ open, paused = false, onClose }: Props) {
  const [index, setIndex] = useState(0);
  const [usingFallback, setUsingFallback] = useState(false);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [, setLayoutTick] = useState(0);
  const bubbleRef = useRef<HTMLDivElement>(null);

  const step = STEPS[index];
  const lastStep = index === STEPS.length - 1;

  /** 走完 / 跳过统一出口：写标记 + 清掉 ?tour=1 + 关引导 */
  const finish = useCallback(() => {
    markTourDone();
    // 桌面版首启会追加 ?tour=1；不清掉的话窗口一刷新就又弹一次引导
    try {
      window.history.replaceState(null, "", window.location.pathname);
    } catch {
      // 历史 API 不可用（极少见）时忽略，localStorage 标记仍然生效
    }
    onClose();
  }, [onClose]);

  const goNext = useCallback(() => {
    if (index >= STEPS.length - 1) {
      finish();
      return;
    }
    setIndex(index + 1);
  }, [finish, index]);

  const goPrev = useCallback(() => {
    if (index <= 0) return;
    setIndex(index - 1);
  }, [index]);

  // 目标不存在时自动向后跳过该步；一步都定位不到就直接收尾，绝不卡住
  useEffect(() => {
    if (!open || paused) return;
    if (resolveStep(step)) return;
    for (let next = index + 1; next < STEPS.length; next += 1) {
      if (resolveStep(STEPS[next])) {
        setIndex(next);
        return;
      }
    }
    finish();
  }, [open, paused, index, step, finish]);

  // 每次提交都重新量一遍：目标 / 气泡尺寸、resize、滚动都会改变高亮位置；
  // 目标不在视野内（例如浏览器恢复了上次的滚动位置）先滚进来再量
  useLayoutEffect(() => {
    if (!open || paused) {
      if (geometry) setGeometry(null);
      return;
    }
    const resolved = resolveStep(step);
    if (!resolved) {
      if (geometry) setGeometry(null);
      return;
    }
    if (usingFallback !== resolved.fallback) {
      setUsingFallback(resolved.fallback);
    }

    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    let rect = resolved.element.getBoundingClientRect();
    // 比视口还高的目标无法完整可见：顶部可见就不再反复滚动
    const canCenter = rect.height <= viewportHeight - SCROLL_MARGIN * 2;
    const fullyVisible =
      rect.top >= SCROLL_MARGIN && rect.bottom <= viewportHeight - SCROLL_MARGIN;
    const topVisible =
      rect.top >= SCROLL_MARGIN && rect.top <= viewportHeight - SCROLL_MARGIN;
    if (canCenter ? !fullyVisible : !topVisible) {
      const top = canCenter
        ? Math.max(
            0,
            window.scrollY + rect.top - (viewportHeight - rect.height) / 2
          )
        : Math.max(0, window.scrollY + rect.top - SCROLL_MARGIN);
      window.scrollTo({ top, behavior: "auto" });
      rect = resolved.element.getBoundingClientRect();
    }

    const bubbleWidth = Math.min(BUBBLE_MAX_WIDTH, viewportWidth - VIEWPORT_MARGIN * 2);
    // offsetHeight 不受动画位移影响，适合拿来做上下翻转判断
    const bubbleHeight = bubbleRef.current?.offsetHeight ?? 0;

    const holeTop = clamp(rect.top - HOLE_PADDING, 0, viewportHeight);
    const holeLeft = clamp(rect.left - HOLE_PADDING, 0, viewportWidth);
    const holeRight = clamp(rect.right + HOLE_PADDING, 0, viewportWidth);
    const holeBottom = clamp(rect.bottom + HOLE_PADDING, 0, viewportHeight);

    const spaceBelow = viewportHeight - rect.bottom - BUBBLE_GAP - VIEWPORT_MARGIN;
    const spaceAbove = rect.top - BUBBLE_GAP - VIEWPORT_MARGIN;
    // 下方放得下就放下方，否则比上下空间，再不行就上翻
    const placeBelow = spaceBelow >= bubbleHeight || spaceBelow >= spaceAbove;
    const rawTop = placeBelow
      ? rect.bottom + BUBBLE_GAP
      : rect.top - BUBBLE_GAP - bubbleHeight;
    const bubbleTop = clamp(
      rawTop,
      VIEWPORT_MARGIN,
      viewportHeight - bubbleHeight - VIEWPORT_MARGIN
    );
    const bubbleLeft = clamp(
      rect.left + rect.width / 2 - bubbleWidth / 2,
      VIEWPORT_MARGIN,
      viewportWidth - bubbleWidth - VIEWPORT_MARGIN
    );

    const next: Geometry = {
      hole: {
        top: Math.round(holeTop),
        left: Math.round(holeLeft),
        width: Math.round(holeRight - holeLeft),
        height: Math.round(holeBottom - holeTop),
      },
      bubble: {
        top: Math.round(bubbleTop),
        left: Math.round(bubbleLeft),
        width: Math.round(bubbleWidth),
      },
    };
    if (!sameGeometry(geometry, next)) setGeometry(next);
  });

  // 引导进行中锁滚动（html + body 双保险），并在隐藏滚动条时补同宽内边距防抖动
  useEffect(() => {
    if (!open || paused) return;
    const root = document.documentElement;
    const body = document.body;
    const prevRootOverflow = root.style.overflow;
    const prevBodyOverflow = body.style.overflow;
    const prevBodyPaddingRight = body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - root.clientWidth;
    root.style.overflow = "hidden";
    body.style.overflow = "hidden";
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`;
    return () => {
      root.style.overflow = prevRootOverflow;
      body.style.overflow = prevBodyOverflow;
      body.style.paddingRight = prevBodyPaddingRight;
    };
  }, [open, paused]);

  // 滚动 / 尺寸变化后重新量位置（capture 把内部滚动容器也算进来）
  useEffect(() => {
    if (!open || paused) return;
    const relayout = () => setLayoutTick((tick) => tick + 1);
    window.addEventListener("resize", relayout);
    window.addEventListener("scroll", relayout, true);
    return () => {
      window.removeEventListener("resize", relayout);
      window.removeEventListener("scroll", relayout, true);
    };
  }, [open, paused]);

  // 键盘：Esc 关闭，方向键前后切换；输入框里的方向键让给输入框
  useEffect(() => {
    if (!open || paused) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        finish();
        return;
      }
      if (event.key === "Tab") {
        // 焦点圈在气泡里，避免 Tab 跑到遮罩后面的页面按钮上误触
        const bubble = bubbleRef.current;
        if (!bubble) return;
        const focusables = Array.from(
          bubble.querySelectorAll<HTMLElement>("button:not([disabled])")
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (active === bubble || !bubble.contains(active)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
          return;
        }
        if (event.shiftKey && active === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
        return;
      }
      if (isTypingTarget(event.target)) return;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        event.preventDefault();
        goNext();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        event.preventDefault();
        goPrev();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, paused, finish, goNext, goPrev]);

  // 打开 / 换步时把焦点收到气泡上，避免方向键落到页面输入框里
  useEffect(() => {
    if (!open || paused) return;
    bubbleRef.current?.focus({ preventScroll: true });
  }, [open, paused, index]);

  if (!open || paused) return null;

  return (
    // 用超大 box-shadow 一次挖空，不额外铺四块遮罩；透明拦截层只负责挡住页面点击
    <div
      className="tour"
      role="dialog"
      aria-modal="true"
      aria-label={`新手引导，第 ${index + 1} 步，共 ${STEPS.length} 步`}
    >
      <div className="tour-block" aria-hidden="true" />
      {geometry && (
        <div
          className="tour-hole"
          aria-hidden="true"
          style={{
            top: geometry.hole.top,
            left: geometry.hole.left,
            width: geometry.hole.width,
            height: geometry.hole.height,
          }}
        />
      )}

      <div
        key={step.key}
        ref={bubbleRef}
        className="tour-bubble"
        tabIndex={-1}
        style={{
          top: geometry?.bubble.top ?? 0,
          left: geometry?.bubble.left ?? 0,
          width: geometry?.bubble.width ?? BUBBLE_MAX_WIDTH,
          visibility: geometry ? "visible" : "hidden",
        }}
      >
        <div className="tour-head">
          <span className="tour-badge">新手引导</span>
          <span className="tour-progress">
            {index + 1} / {STEPS.length}
          </span>
        </div>

        <p className="tour-text">{step.text}</p>
        {usingFallback && step.fallbackNote && (
          <p className="tour-note">{step.fallbackNote}</p>
        )}

        <div className="tour-foot">
          <div className="tour-dots" aria-hidden="true">
            {STEPS.map((item, dotIndex) => (
              <span
                key={item.key}
                className={`tour-dot ${dotIndex === index ? "tour-dot-on" : ""} ${
                  dotIndex < index ? "tour-dot-done" : ""
                }`}
              />
            ))}
          </div>
          <div className="tour-actions">
            <button
              type="button"
              className="btn btn-sm tour-skip"
              onClick={finish}
            >
              跳过
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={index === 0}
              onClick={goPrev}
            >
              上一步
            </button>
            <button type="button" className="btn btn-primary btn-sm" onClick={goNext}>
              {lastStep ? "开始使用" : "下一步"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
