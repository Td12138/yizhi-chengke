import { useEffect, useMemo, useState } from "react";
import type { Explain } from "../types";
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
  AXIS_FONT,
} from "../lib/plot-theme";

const WIDTH = 420;
const HEIGHT = 260;
const PADDING = 30;
const PLOT_LEFT = PADDING;
const PLOT_RIGHT = WIDTH - PADDING;
const PLOT_TOP = PADDING;
const PLOT_BOTTOM = HEIGHT - PADDING;
// 纵轴显示范围：固定值保证拖动滑块时坐标系不跳动
const Y_LIMIT = 8;
const STEPS = 180;
// 滑块范围：后端给的 a 超出区间时夹到边界，免得滑块位置和曲线对不上
const A_MIN = -3;
const A_MAX = 3;
const A_STEP = 0.1;
// 公式里保留的小数位：避免 0.30000000000000004 这类浮点噪声
const B_FORMULA_DIGITS = 2;
const C_FORMULA_DIGITS = 2;
/** 顶点坐标标注的小数位 */
const VERTEX_DIGITS = 2;
/** 滑块值与题目给定 a 的偏差阈值：超过才算用户手动拖动 */
const A_DRIFT_EPSILON = 1e-4;

const TICK_FONT_SIZE = 12;
const AXIS_NAME_FONT_SIZE = 14;
/** 顶点坐标标注字号 */
const VERTEX_FONT_SIZE = 12;
/** 原点 O 的字号：比刻度稍大一号，和轴名区分开 */
const ORIGIN_FONT_SIZE = 13;
const CAPTION_FONT_SIZE = 12;
/** 刻度数字与轴线之间的间隙 */
const TICK_GAP = 5;
/** 刻度数字基线相对轴线的偏移 */
const TICK_TEXT_OFFSET = TICK_GAP + TICK_FONT_SIZE;
/** 刻度数字的估计占位宽度：用于判断轴旁的标注会不会被裁掉 */
const TICK_LABEL_WIDTH = 24;
/** 数字基线相对纵坐标的微调（让数字视觉居中于网格线） */
const TICK_BASELINE_SHIFT = TICK_FONT_SIZE * 0.35;
/** 原点 O 再往下让开 3px，避免和横轴刻度叠在一起 */
const ORIGIN_DROP = 3;
/** 横轴名 x 相对轴线的抬升量（再叠一个 TICK_GAP） */
const AXIS_NAME_LIFT = 2;
/** 纵轴名 y 相对轴线的横移量 */
const AXIS_NAME_NUDGE = 1;

/** 网格：横向按整数步长；纵向固定这几条，避免与刻度数字打架 */
const GRID_X_STEP = 1;
const GRID_Y_TICKS = [-6, -4, -2, 2, 4, 6] as const;
/** 区间特别宽时自动放大网格步长，线不至于糊成一片 */
const MAX_GRID_LINES = 40;

/** 顶点标记：纸色外圈 + 朱砂实心点 */
const VERTEX_RING_R = 5;
const VERTEX_DOT_R = 2.8;
const VERTEX_RING_WIDTH = 1;
/** 坐标标注与顶点圆点的距离 */
const VERTEX_LABEL_GAP = 9;
/** 坐标标注的估计占位宽度：贴右边界时自动翻到左侧 */
const VERTEX_LABEL_WIDTH = 86;
const VERTEX_LABEL_LIFT = 9;
const VERTEX_LABEL_DROP = 18;

/** 画图必需的参数名：缺一个就不出图（只有文字讲解） */
const REQUIRED_PARAMS = ["a", "b", "c", "x_min", "x_max"] as const;

type SliderParams = {
  a: number;
  b: number;
  c: number;
  xMin: number;
  xMax: number;
};

function buildPath(
  a: number,
  b: number,
  c: number,
  xMin: number,
  xMax: number
): string {
  const commands: string[] = [];
  let drawing = false;
  for (let i = 0; i <= STEPS; i++) {
    const x = xMin + ((xMax - xMin) * i) / STEPS;
    const y = a * x * x + b * x + c;
    if (!Number.isFinite(y) || y < -Y_LIMIT || y > Y_LIMIT) {
      drawing = false;
      continue;
    }
    const px = PADDING + ((x - xMin) / (xMax - xMin)) * (WIDTH - PADDING * 2);
    const py = PADDING + ((Y_LIMIT - y) / (2 * Y_LIMIT)) * (HEIGHT - PADDING * 2);
    commands.push(`${drawing ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`);
    drawing = true;
  }
  return commands.join(" ");
}

/** 横向网格线的 x 值：整数步长，区间过宽时按 MAX_GRID_LINES 自动放大步长 */
function buildGridTicks(xMin: number, xMax: number): number[] {
  const step = Math.max(GRID_X_STEP, Math.ceil((xMax - xMin) / MAX_GRID_LINES));
  const values: number[] = [];
  for (let i = Math.ceil(xMin / step); i <= Math.floor(xMax / step); i += 1) {
    values.push(i * step);
  }
  return values;
}

/** 只接受有限数（数字或纯数字字符串），其余一律当缺失 */
function toFinite(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * 校验后端给的 params：真实模型偶尔漏字段或给字符串，
 * 缺一个或有一个非有限数就返回 null，交给「只显示文字讲解」的降级路径。
 */
function readParams(raw: unknown): SliderParams | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  const values: number[] = [];
  for (const key of REQUIRED_PARAMS) {
    const value = toFinite(source[key]);
    if (value === null) return null;
    values.push(value);
  }
  const [a, b, c, xMin, xMax] = values;
  // 区间长度为 0 / 反向时无法画图（会除零或反转坐标轴）
  if (xMax <= xMin) return null;
  return { a, b, c, xMin, xMax };
}

function clampA(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) return 0;
  return Math.min(A_MAX, Math.max(A_MIN, value));
}

/** 去掉浮点噪声：-0 也归成 0 */
function trimNumber(value: number, digits: number): string {
  const rounded = Number(value.toFixed(digits));
  return String(Object.is(rounded, -0) ? 0 : rounded);
}

export default function FunctionSlider({ explain }: { explain: Explain }) {
  const params = useMemo(() => readParams(explain.params), [explain.params]);
  const baseA = clampA(params?.a);
  const [a, setA] = useState(baseA);

  // 同一题重新判分（key 不变）时把滑块拉回题目给定的 a
  useEffect(() => {
    if (params) setA(clampA(params.a));
  }, [params]);

  const path = useMemo(
    () =>
      params
        ? buildPath(a, params.b, params.c, params.xMin, params.xMax)
        : "",
    [a, params]
  );

  // 参数不全 / 非数字：不画图也不崩，讲解文字由 App.tsx 的 explain-script 展示
  if (!params) return null;

  const { b, c, xMin, xMax } = params;

  const toX = (x: number) =>
    PADDING + ((x - xMin) / (xMax - xMin)) * (WIDTH - PADDING * 2);
  const toY = (y: number) =>
    PADDING + ((Y_LIMIT - y) / (2 * Y_LIMIT)) * (HEIGHT - PADDING * 2);

  const axisY = toY(0);
  const axisX = toX(0);
  // 纵轴贴左边界时，刻度数字改到轴右侧，避免被画布裁掉
  const yLabelsOnRight = axisX - TICK_GAP - TICK_LABEL_WIDTH < PLOT_LEFT;
  const yLabelX = yLabelsOnRight ? axisX + TICK_GAP : axisX - TICK_GAP;
  const yLabelAnchor = yLabelsOnRight ? "start" : "end";

  const gridX = buildGridTicks(xMin, xMax);

  const vertexX = a === 0 ? 0 : -b / (2 * a);
  const vertexY = a * vertexX * vertexX + b * vertexX + c;
  const vertexVisible =
    a !== 0 &&
    vertexX >= xMin &&
    vertexX <= xMax &&
    vertexY >= -Y_LIMIT &&
    vertexY <= Y_LIMIT;
  const vertexPx = toX(vertexX);
  const vertexPy = toY(vertexY);
  // 贴右边界的顶点把坐标标注翻到左侧，贴顶时落到点下方
  const vertexLabelRight = vertexPx + VERTEX_LABEL_GAP + VERTEX_LABEL_WIDTH > PLOT_RIGHT;
  const vertexLabelX = vertexLabelRight
    ? Math.max(PLOT_LEFT, vertexPx - VERTEX_LABEL_GAP)
    : vertexPx + VERTEX_LABEL_GAP;
  const vertexLabelAnchor = vertexLabelRight ? "end" : "start";
  const vertexLabelY =
    vertexPy - VERTEX_LABEL_LIFT - TICK_FONT_SIZE >= PLOT_TOP
      ? vertexPy - VERTEX_LABEL_LIFT
      : vertexPy + VERTEX_LABEL_DROP;

  const drifted = Math.abs(a - baseA) > A_DRIFT_EPSILON;
  const direction = a > 0 ? "开口向上" : a < 0 ? "开口向下" : "退化为直线";

  return (
    <div className="slider-viz">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="二次函数图象"
        style={FIGURE_STYLE}
      >
        {/* 网格：先铺线，坐标轴压在上面 */}
        {gridX.map((x) => (
          <line
            key={`gx-${x}`}
            x1={toX(x)}
            y1={PLOT_TOP}
            x2={toX(x)}
            y2={PLOT_BOTTOM}
            stroke={COLOR_GRID}
            strokeWidth={GRID_WIDTH}
          />
        ))}
        {GRID_Y_TICKS.map((y) => (
          <line
            key={`gy-${y}`}
            x1={PLOT_LEFT}
            y1={toY(y)}
            x2={PLOT_RIGHT}
            y2={toY(y)}
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

        {/* 刻度数字：横轴整数、纵轴 [-6, 6] 的偶数刻度，原点单独标 O */}
        {gridX.map((x) =>
          x === 0 ? null : (
            <text
              key={`tx-${x}`}
              x={toX(x)}
              y={axisY + TICK_TEXT_OFFSET}
              textAnchor="middle"
              fontSize={TICK_FONT_SIZE}
              fill={COLOR_TICK}
              fontFamily={AXIS_FONT}
            >
              {trimNumber(x, 0)}
            </text>
          )
        )}
        {GRID_Y_TICKS.map((y) => (
          <text
            key={`ty-${y}`}
            x={yLabelX}
            y={toY(y) + TICK_BASELINE_SHIFT}
            textAnchor={yLabelAnchor}
            fontSize={TICK_FONT_SIZE}
            fill={COLOR_TICK}
            fontFamily={AXIS_FONT}
          >
            {trimNumber(y, 0)}
          </text>
        ))}

        {/* 轴名：衬线斜体 */}
        <text
          x={PLOT_RIGHT - ARROW_LENGTH - TICK_GAP}
          y={axisY - TICK_GAP - AXIS_NAME_LIFT}
          textAnchor="end"
          fontSize={AXIS_NAME_FONT_SIZE}
          fontStyle="italic"
          fontFamily={AXIS_FONT}
          fill={COLOR_INK}
        >
          x
        </text>
        <text
          x={axisX + TICK_GAP + AXIS_NAME_NUDGE}
          y={PLOT_TOP + AXIS_NAME_FONT_SIZE}
          textAnchor="start"
          fontSize={AXIS_NAME_FONT_SIZE}
          fontStyle="italic"
          fontFamily={AXIS_FONT}
          fill={COLOR_INK}
        >
          y
        </text>
        <text
          x={yLabelX}
          y={axisY + TICK_TEXT_OFFSET + ORIGIN_DROP}
          textAnchor={yLabelAnchor}
          fontSize={ORIGIN_FONT_SIZE}
          fontStyle="italic"
          fontFamily={AXIS_FONT}
          fill={COLOR_INK}
        >
          O
        </text>

        {/* 函数曲线：松烟绿，干净锐利 */}
        <path
          d={path}
          fill="none"
          stroke={COLOR_CURVE}
          strokeWidth={CURVE_WIDTH}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* 顶点：对称轴虚线 + 到纵轴的水平虚线 + 白圈朱砂点 + 坐标标注 */}
        {vertexVisible && (
          <>
            <line
              x1={vertexPx}
              y1={PLOT_TOP}
              x2={vertexPx}
              y2={PLOT_BOTTOM}
              className="guide"
            />
            <line
              x1={vertexPx}
              y1={vertexPy}
              x2={axisX}
              y2={vertexPy}
              className="guide"
            />
            <circle
              cx={vertexPx}
              cy={vertexPy}
              r={VERTEX_RING_R}
              fill={COLOR_PAPER}
              stroke={COLOR_INK}
              strokeWidth={VERTEX_RING_WIDTH}
            />
            <circle cx={vertexPx} cy={vertexPy} r={VERTEX_DOT_R} fill={COLOR_MARK} />
            <text
              x={vertexLabelX}
              y={vertexLabelY}
              textAnchor={vertexLabelAnchor}
              fontSize={VERTEX_FONT_SIZE}
              fontFamily={AXIS_FONT}
              fill={COLOR_INK}
            >
              ({trimNumber(vertexX, VERTEX_DIGITS)},{" "}
              {trimNumber(vertexY, VERTEX_DIGITS)})
            </text>
          </>
        )}
      </svg>

      <div className="slider-row" style={FIGURE_STYLE}>
        <label htmlFor="slider-a">a = {a.toFixed(1)}</label>
        <input
          id="slider-a"
          type="range"
          min={A_MIN}
          max={A_MAX}
          step={A_STEP}
          value={a}
          onChange={(event) => {
            const next = Number(event.target.value);
            setA(Number.isFinite(next) ? next : 0);
          }}
        />
        <span
          style={{
            color: COLOR_TICK,
            fontSize: CAPTION_FONT_SIZE,
            fontFamily: AXIS_FONT,
            whiteSpace: "nowrap",
          }}
        >
          {direction}
        </span>
        {drifted && (
          <button
            type="button"
            className="sample-chip"
            title={`恢复为题目给定的 a = ${baseA.toFixed(1)}`}
            onClick={() => setA(baseA)}
          >
            复位
          </button>
        )}
      </div>

      <div className="formula-line" style={FIGURE_STYLE}>
        y = {a.toFixed(1)}x² {b >= 0 ? "+" : "−"}{" "}
        {trimNumber(Math.abs(b), B_FORMULA_DIGITS)}x{" "}
        {c >= 0 ? "+" : "−"} {trimNumber(Math.abs(c), C_FORMULA_DIGITS)}
        {vertexVisible && (
          <span className="vertex-hint">
            {" "}
            顶点 ({vertexX.toFixed(VERTEX_DIGITS)}, {vertexY.toFixed(VERTEX_DIGITS)})
          </span>
        )}
      </div>
    </div>
  );
}
