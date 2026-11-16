import { useMemo } from "react";
import {
  ARROW_HALF_WIDTH,
  ARROW_LENGTH,
  AXIS_WIDTH,
  COLOR_CURVE,
  COLOR_GRID,
  COLOR_INK,
  COLOR_MARK,
  COLOR_PAPER,
  COLOR_TICK,
  CURVE_WIDTH,
  FIGURE_STYLE,
  GRID_WIDTH,
  SERIF,
} from "../lib/plot-theme";

/**
 * 轻量函数图像渲染器：自带表达式解析（递归下降），不依赖 eval 与绘图库。
 * 支持 + - * / ^ ( )、一元负号、sin cos tan asin acos atan sqrt abs ln log exp、
 * 常量 pi / e、隐式乘法（2x、3(x+1)）。
 */

/* 画布与滑块图对齐（同 420 宽），缩放后刻度字不会被压到 7–8px */
const WIDTH = 420;
const HEIGHT = 260;
const PADDING = 34;
const PLOT_LEFT = PADDING;
const PLOT_RIGHT = WIDTH - PADDING;
const PLOT_TOP = PADDING;
const PLOT_BOTTOM = HEIGHT - PADDING;
const SAMPLES = 480;
// 纵轴自适应上限：再大就压成一条平线，不如裁掉两侧看形状
const Y_DISPLAY_MAX = 12;
/** 纵轴上限取「整齐」的档位：刻度才不会出现 0.62 / 1.24 这种非教材数字 */
const Y_LIMIT_STEPS = [1, 2, 3, 4, 5, 6, 8, 10, 12] as const;
/** 采样值到纵轴上限的余量 */
const Y_HEADROOM = 1.25;

/** 采样点序号化成 x 值时用的位数：避免 0.30000000000000004 之类的噪声 */
const TICK_DIGITS = 2;
/** 判断刻度是否就是原点用的小阈值 */
const TICK_ZERO_EPSILON = 1e-9;

const TICK_FONT_SIZE = 12;
const AXIS_NAME_FONT_SIZE = 14;
/** 刻度数字与轴线之间的间隙 */
const TICK_GAP = 5;
/** 刻度数字基线相对轴线的偏移 */
const TICK_TEXT_OFFSET = TICK_GAP + TICK_FONT_SIZE;
/** 刻度数字的估计占位宽度：纵向刻度贴左边界时改到轴右侧 */
const TICK_LABEL_WIDTH = 26;
/** 数字基线相对纵坐标的微调（视觉居中于网格线） */
const TICK_BASELINE_SHIFT = TICK_FONT_SIZE * 0.35;
/** 纵轴刻度文字离画布下沿留的空隙 */
const TICK_BOTTOM_INSET = 2;
/** 横轴名 x 相对轴线的抬升量（再叠一个 TICK_GAP） */
const AXIS_NAME_LIFT = 2;
/** 纵轴名 y 相对轴线的横移量 */
const AXIS_NAME_NUDGE = 1;
/** 出框提示压在坐标轴上的纸色描边宽度 */
const HINT_HALO_WIDTH = 4;

/** 横向网格线条数：13 条线 / 12 格 */
const GRID_DIVISIONS = 12;
/** 纵向网格线所在的位置：yLimit 的 ±1/2 与 ±1 */
const Y_GRID_FACTORS = [-2, -1, 1, 2] as const;
/** 13 条网格线里只标中间 5 条：两端留给箭头，原点单独标 0 */
const X_LABEL_INDEXES = [2, 4, 6, 8, 10] as const;

/** 零点实心朱砂点；截距用空心墨圈，教材里区分「根」和「截距」的老办法 */
const MARKER_R = 3.5;
const MARKER_RING_R = 4.5;
const MARKER_STROKE_WIDTH = 1.4;

/** 零点检测：最多标几个、相邻零点最小间距、跳变判据与采样邻接判据 */
const MAX_ZEROS = 6;
const ZERO_MIN_GAP = 0.15;
const JUMP_RATIO = 0.8;
const SAMPLE_ADJACENCY = 1.5;
/** 页脚最多直接列出几个零点坐标，多了只报个数 */
const FOOT_ZERO_VALUES = 3;

type Fn = (x: number) => number;

const FUNCTIONS: Record<string, (v: number) => number> = {
  sin: Math.sin,
  cos: Math.cos,
  tan: Math.tan,
  asin: Math.asin,
  acos: Math.acos,
  atan: Math.atan,
  sqrt: Math.sqrt,
  abs: Math.abs,
  ln: Math.log,
  log: Math.log10,
  exp: Math.exp,
};

const CONSTANTS: Record<string, number> = { pi: Math.PI, e: Math.E };

function tokenize(input: string): string[] {
  const tokens: string[] = [];
  let index = 0;
  while (index < input.length) {
    const char = input[index];
    if (/\s/.test(char)) {
      index += 1;
      continue;
    }
    if (/[0-9.]/.test(char)) {
      let number = "";
      while (index < input.length && /[0-9.]/.test(input[index])) {
        number += input[index];
        index += 1;
      }
      tokens.push(number);
      continue;
    }
    if (/[a-zA-Z]/.test(char)) {
      let name = "";
      while (index < input.length && /[a-zA-Z]/.test(input[index])) {
        name += input[index];
        index += 1;
      }
      tokens.push(name.toLowerCase());
      continue;
    }
    if ("+-*/^()".includes(char)) {
      tokens.push(char);
      index += 1;
      continue;
    }
    throw new Error(`无法识别的字符「${char}」`);
  }
  return tokens;
}

export function parseExpression(input: string): Fn {
  const tokens = tokenize(input);
  let pos = 0;

  const peek = () => tokens[pos];
  const next = () => tokens[pos++];

  function parseSum(): Fn {
    let left = parseProduct();
    while (peek() === "+" || peek() === "-") {
      const op = next();
      const right = parseProduct();
      const prev = left;
      left = op === "+" ? (x) => prev(x) + right(x) : (x) => prev(x) - right(x);
    }
    return left;
  }

  function parseProduct(): Fn {
    let left = parseUnary();
    while (true) {
      const token = peek();
      if (token === "*" || token === "/") {
        next();
        const right = parseUnary();
        const prev = left;
        left = token === "*" ? (x) => prev(x) * right(x) : (x) => prev(x) / right(x);
        continue;
      }
      // 隐式乘法：2x、3(x+1)、(x+1)(x-1)
      if (
        token !== undefined &&
        (token === "(" || /^[0-9.]$/.test(token[0]) || /^[a-zA-Z]/.test(token))
      ) {
        const right = parseUnary();
        const prev = left;
        left = (x) => prev(x) * right(x);
        continue;
      }
      break;
    }
    return left;
  }

  function parseUnary(): Fn {
    if (peek() === "-") {
      next();
      const operand = parseUnary();
      return (x) => -operand(x);
    }
    if (peek() === "+") {
      next();
      return parseUnary();
    }
    return parsePower();
  }

  function parsePower(): Fn {
    const base = parsePrimary();
    if (peek() === "^") {
      next();
      const exponent = parseUnary(); // 右结合
      return (x) => Math.pow(base(x), exponent(x));
    }
    return base;
  }

  function parsePrimary(): Fn {
    const token = next();
    if (token === undefined) throw new Error("表达式不完整");
    if (token === "(") {
      const inner = parseSum();
      if (next() !== ")") throw new Error("括号不匹配");
      return inner;
    }
    if (/^[0-9.]/.test(token)) {
      const value = Number(token);
      if (Number.isNaN(value)) throw new Error(`数字格式错误：${token}`);
      return () => value;
    }
    if (token === "x") return (x) => x;
    if (token in CONSTANTS) {
      const value = CONSTANTS[token];
      return () => value;
    }
    if (token in FUNCTIONS) {
      const fn = FUNCTIONS[token];
      if (next() !== "(") throw new Error(`函数 ${token} 后面需要括号`);
      const inner = parseSum();
      if (next() !== ")") throw new Error("括号不匹配");
      return (x) => fn(inner(x));
    }
    throw new Error(`未知符号「${token}」`);
  }

  const fn = parseSum();
  if (pos < tokens.length) throw new Error(`多余的内容「${tokens[pos]}」`);
  return fn;
}

/** 刻度文字：最多 TICK_DIGITS 位小数，-0 归成 0 */
function formatTick(value: number): string {
  const rounded = Number(value.toFixed(TICK_DIGITS));
  return String(Object.is(rounded, -0) ? 0 : rounded);
}

/** 纵轴上限吸附到整齐档位（1/2/3/4/5/6/8/10/12），刻度数字好读 */
function snapYLimit(raw: number): number {
  for (const step of Y_LIMIT_STEPS) {
    if (raw <= step) return step;
  }
  return Y_DISPLAY_MAX;
}

/**
 * 零点近似：相邻采样点异号即插值。
 * 同号跳过；跨过 1/x、tan 的渐近线时值会剧烈跳变，按跃变阈值排除；
 * 中间有非有限值被跳过（采样不连续）也不算。
 */
function findZeros(
  points: { x: number; y: number }[],
  yLimit: number,
  stepX: number
): number[] {
  const zeros: number[] = [];
  for (let i = 0; i + 1 < points.length; i += 1) {
    const p1 = points[i];
    const p2 = points[i + 1];
    if (Math.abs(p2.x - p1.x) > stepX * SAMPLE_ADJACENCY) continue;
    if (p1.y === p2.y) continue;
    if (p1.y * p2.y > 0) continue;
    if (Math.abs(p1.y) > yLimit || Math.abs(p2.y) > yLimit) continue;
    if (Math.abs(p2.y - p1.y) >= yLimit * JUMP_RATIO) continue;
    const xZero = p1.x + ((p2.x - p1.x) * p1.y) / (p1.y - p2.y);
    if (!Number.isFinite(xZero)) continue;
    const last = zeros[zeros.length - 1];
    if (last !== undefined && Math.abs(xZero - last) <= ZERO_MIN_GAP) continue;
    zeros.push(xZero);
    if (zeros.length >= MAX_ZEROS) break;
  }
  return zeros;
}

type PlotData = {
  points: { x: number; y: number }[];
  yLimit: number;
  zeros: number[];
  intercept: number | null;
};

type Props = {
  expression: string;
  xMin?: number;
  xMax?: number;
};

export default function FunctionPlot({ expression, xMin = -6, xMax = 6 }: Props) {
  const result = useMemo<PlotData | { error: string }>(() => {
    let fn: Fn;
    try {
      fn = parseExpression(expression);
    } catch (err) {
      if (err instanceof RangeError) {
        // 括号 / 幂次嵌套过深时解析器自身也可能爆栈
        return { error: "表达式嵌套过深，无法解析，请简化后再试" };
      }
      return { error: err instanceof Error ? err.message : String(err) };
    }

    const points: { x: number; y: number }[] = [];
    for (let i = 0; i <= SAMPLES; i++) {
      const x = xMin + ((xMax - xMin) * i) / SAMPLES;
      let y: number;
      try {
        y = fn(x);
      } catch (err) {
        // 嵌套过深的表达式在求值时可能抛 RangeError（调用栈溢出）：整幅图按错误提示处理，不白屏
        const reason = err instanceof RangeError ? "表达式嵌套过深" : "表达式求值失败";
        return { error: `${reason}，无法绘制，请简化后再试` };
      }
      if (Number.isFinite(y)) points.push({ x, y });
    }
    if (points.length === 0) {
      return { error: "这个表达式在给定区间内没有有效取值" };
    }

    const magnitudes = points.map((p) => Math.abs(p.y)).sort((a, b) => a - b);
    const p90 = magnitudes[Math.floor(magnitudes.length * 0.9)] || 1;
    const yLimit = snapYLimit(Math.max(1, p90 * Y_HEADROOM));

    // 与纵轴的交点：只在可视高度内标，否则点会跑到画布外
    let intercept: number | null = null;
    if (xMin <= 0 && xMax >= 0) {
      try {
        const value = fn(0);
        if (Number.isFinite(value) && Math.abs(value) <= yLimit) intercept = value;
      } catch {
        intercept = null;
      }
    }

    const zeros = findZeros(points, yLimit, (xMax - xMin) / SAMPLES);
    return { points, yLimit, zeros, intercept };
  }, [expression, xMin, xMax]);

  const toX = (x: number) => PADDING + ((x - xMin) / (xMax - xMin)) * (WIDTH - PADDING * 2);

  if ("error" in result) {
    return (
      <div className="plot">
        <p className="plot-error">{result.error}</p>
      </div>
    );
  }

  const { points, yLimit, zeros, intercept } = result;
  const toY = (y: number) => PADDING + ((yLimit - y) / (2 * yLimit)) * (HEIGHT - PADDING * 2);

  let path = "";
  let drawing = false;
  for (const point of points) {
    if (Math.abs(point.y) > yLimit) {
      drawing = false;
      continue;
    }
    path += `${drawing ? "L" : "M"}${toX(point.x).toFixed(1)},${toY(point.y).toFixed(1)}`;
    drawing = true;
  }

  const axisY = toY(0);
  const axisX = toX(0);
  const tickStep = (xMax - xMin) / GRID_DIVISIONS;
  const yAxisVisible = xMin <= 0 && xMax >= 0;
  // 纵轴贴左边界时，刻度数字改到轴右侧，避免被画布裁掉
  const yLabelsOnRight = axisX - TICK_GAP - TICK_LABEL_WIDTH < PLOT_LEFT;
  const yLabelX = yLabelsOnRight ? axisX + TICK_GAP : axisX - TICK_GAP;
  const yLabelAnchor = yLabelsOnRight ? "start" : "end";

  // 整条曲线都超出可视纵轴：path 为空，得明说一句，否则只剩一张空网格
  const offCanvas = points.length > 0 && path === "";
  // 与纵轴交点在原点时，朱砂零点已经标在那儿，不再叠一个空心圈
  const interceptAtOrigin =
    intercept !== null && Math.abs(intercept) < TICK_ZERO_EPSILON;

  const summary: string[] = [];
  if (intercept !== null) summary.push(`f(0) = ${formatTick(intercept)}`);
  if (zeros.length > 0) {
    summary.push(
      zeros.length <= FOOT_ZERO_VALUES
        ? `零点 x ≈ ${zeros.map((value) => formatTick(value)).join(", ")}`
        : `零点多达 ${zeros.length} 个`
    );
  } else {
    summary.push("区间内未检出零点");
  }
  if (offCanvas) summary.push("曲线超出当前纵轴显示范围");

  return (
    <div className="plot">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`函数图像 ${expression}`}
        style={FIGURE_STYLE}
      >
        {/* 网格：先铺线，坐标轴压在上面 */}
        {Array.from({ length: GRID_DIVISIONS + 1 }, (_, index) => {
          const x = xMin + tickStep * index;
          return (
            <line
              key={`gx-${index}`}
              x1={toX(x)}
              y1={PLOT_TOP}
              x2={toX(x)}
              y2={PLOT_BOTTOM}
              stroke={COLOR_GRID}
              strokeWidth={GRID_WIDTH}
            />
          );
        })}
        {Y_GRID_FACTORS.map((factor) => (
          <line
            key={`gy-${factor}`}
            x1={PLOT_LEFT}
            y1={toY((yLimit * factor) / 2)}
            x2={PLOT_RIGHT}
            y2={toY((yLimit * factor) / 2)}
            stroke={COLOR_GRID}
            strokeWidth={GRID_WIDTH}
          />
        ))}

        {/* 坐标轴：墨色细线 + 末端实心小三角 */}
        <line
          x1={PLOT_LEFT}
          y1={axisY}
          x2={PLOT_RIGHT - ARROW_LENGTH}
          y2={axisY}
          stroke={COLOR_INK}
          strokeWidth={AXIS_WIDTH}
        />
        <path
          d={`M${PLOT_RIGHT},${axisY} L${PLOT_RIGHT - ARROW_LENGTH},${
            axisY - ARROW_HALF_WIDTH
          } L${PLOT_RIGHT - ARROW_LENGTH},${axisY + ARROW_HALF_WIDTH} Z`}
          fill={COLOR_INK}
        />
        {yAxisVisible && (
          <>
            <line
              x1={axisX}
              y1={PLOT_BOTTOM}
              x2={axisX}
              y2={PLOT_TOP + ARROW_LENGTH}
              stroke={COLOR_INK}
              strokeWidth={AXIS_WIDTH}
            />
            <path
              d={`M${axisX},${PLOT_TOP} L${axisX - ARROW_HALF_WIDTH},${
                PLOT_TOP + ARROW_LENGTH
              } L${axisX + ARROW_HALF_WIDTH},${PLOT_TOP + ARROW_LENGTH} Z`}
              fill={COLOR_INK}
            />
          </>
        )}

        {/* 刻度数字：横轴标中间 5 格，纵轴标 yLimit 的 ±1/2 与 ±1 */}
        {X_LABEL_INDEXES.map((index) => {
          const value = xMin + tickStep * index;
          if (Math.abs(value) < TICK_ZERO_EPSILON) return null;
          return (
            <text
              key={`tx-${index}`}
              x={toX(value)}
              y={axisY + TICK_TEXT_OFFSET}
              textAnchor="middle"
              fontSize={TICK_FONT_SIZE}
              fill={COLOR_TICK}
              fontFamily={SERIF}
            >
              {formatTick(value)}
            </text>
          );
        })}
        {yAxisVisible &&
          Y_GRID_FACTORS.map((factor) => {
            const value = (yLimit * factor) / 2;
            return (
              <text
                key={`ty-${factor}`}
                x={yLabelX}
                y={Math.min(
                  PLOT_BOTTOM - TICK_BOTTOM_INSET,
                  Math.max(TICK_FONT_SIZE, toY(value) + TICK_BASELINE_SHIFT)
                )}
                textAnchor={yLabelAnchor}
                fontSize={TICK_FONT_SIZE}
                fill={COLOR_TICK}
                fontFamily={SERIF}
              >
                {formatTick(value)}
              </text>
            );
          })}

        {/* 轴名与原点：衬线斜体 */}
        <text
          x={PLOT_RIGHT - ARROW_LENGTH - TICK_GAP}
          y={axisY - TICK_GAP - AXIS_NAME_LIFT}
          textAnchor="end"
          fontSize={AXIS_NAME_FONT_SIZE}
          fontStyle="italic"
          fontFamily={SERIF}
          fill={COLOR_INK}
        >
          x
        </text>
        {yAxisVisible && (
          <>
            <text
              x={axisX + TICK_GAP + AXIS_NAME_NUDGE}
              y={PLOT_TOP + AXIS_NAME_FONT_SIZE}
              textAnchor="start"
              fontSize={AXIS_NAME_FONT_SIZE}
              fontStyle="italic"
              fontFamily={SERIF}
              fill={COLOR_INK}
            >
              y
            </text>
            <text
              x={yLabelX}
              y={axisY + TICK_TEXT_OFFSET}
              textAnchor={yLabelAnchor}
              fontSize={AXIS_NAME_FONT_SIZE}
              fontStyle="italic"
              fontFamily={SERIF}
              fill={COLOR_INK}
            >
              O
            </text>
          </>
        )}

        {/* 函数曲线：松烟绿，干净锐利 */}
        <path
          d={path}
          fill="none"
          stroke={COLOR_CURVE}
          strokeWidth={CURVE_WIDTH}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* 曲线整体跑到可视纵轴之外：在横轴上方直说一句，别只留一张空网格 */}
        {offCanvas && (
          <text
            x={(PLOT_LEFT + PLOT_RIGHT) / 2}
            y={axisY - TICK_TEXT_OFFSET}
            textAnchor="middle"
            fontSize={TICK_FONT_SIZE}
            fontFamily={SERIF}
            fill={COLOR_TICK}
            stroke={COLOR_PAPER}
            strokeWidth={HINT_HALO_WIDTH}
            paintOrder="stroke"
          >
            曲线超出当前纵轴显示范围
          </text>
        )}

        {/* 关键点：截距空心墨圈、零点朱砂实心点 */}
        {intercept !== null && !interceptAtOrigin && (
          <circle
            cx={toX(0)}
            cy={toY(intercept)}
            r={MARKER_RING_R}
            fill={COLOR_PAPER}
            stroke={COLOR_INK}
            strokeWidth={MARKER_STROKE_WIDTH}
          >
            <title>{`f(0) = ${formatTick(intercept)}`}</title>
          </circle>
        )}
        {zeros.map((xZero, index) => (
          <circle
            key={`zero-${index}`}
            cx={toX(xZero)}
            cy={axisY}
            r={MARKER_R}
            fill={COLOR_MARK}
            stroke={COLOR_PAPER}
            strokeWidth={MARKER_STROKE_WIDTH}
          >
            <title>{`x ≈ ${formatTick(xZero)}`}</title>
          </circle>
        ))}
      </svg>
      <div
        className="plot-foot"
        style={{ ...FIGURE_STYLE, flexWrap: "wrap", rowGap: 2 }}
      >
        <span>
          x ∈ [{formatTick(xMin)}, {formatTick(xMax)}] · y ∈ [±{yLimit.toFixed(1)}]
        </span>
        <span>{summary.join(" · ")}</span>
      </div>
    </div>
  );
}
