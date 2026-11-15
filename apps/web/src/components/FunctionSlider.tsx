import { useEffect, useMemo, useState } from "react";
import type { Explain } from "../types";

const WIDTH = 420;
const HEIGHT = 260;
const PADDING = 30;
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
    if (y < -Y_LIMIT || y > Y_LIMIT) {
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
  // 区间长度为 0 / 反向时无法画图（会除零）
  if (xMax === xMin) return null;
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
  const [a, setA] = useState(() => clampA(params?.a));

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
  const vertexX = a === 0 ? 0 : -b / (2 * a);
  const vertexY = a * vertexX * vertexX + b * vertexX + c;
  const vertexVisible =
    a !== 0 &&
    vertexX >= xMin &&
    vertexX <= xMax &&
    vertexY >= -Y_LIMIT &&
    vertexY <= Y_LIMIT;

  return (
    <div className="slider-viz">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="二次函数图象">
        <line
          x1={PADDING}
          y1={axisY}
          x2={WIDTH - PADDING}
          y2={axisY}
          className="axis"
        />
        <line
          x1={toX(0)}
          y1={PADDING}
          x2={toX(0)}
          y2={HEIGHT - PADDING}
          className="axis"
        />
        <path d={path} className="curve" />
        {vertexVisible && (
          <>
            <line
              x1={toX(vertexX)}
              y1={PADDING}
              x2={toX(vertexX)}
              y2={HEIGHT - PADDING}
              className="guide"
            />
            <circle
              cx={toX(vertexX)}
              cy={toY(vertexY)}
              r={4.5}
              className="vertex"
            />
          </>
        )}
      </svg>

      <div className="slider-row">
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
      </div>

      <div className="formula-line">
        y = {a.toFixed(1)}x² {b >= 0 ? "+" : "−"}{" "}
        {trimNumber(Math.abs(b), B_FORMULA_DIGITS)}x{" "}
        {c >= 0 ? "+" : "−"} {trimNumber(Math.abs(c), C_FORMULA_DIGITS)}
        {vertexVisible && (
          <span className="vertex-hint">
            {" "}
            顶点 ({vertexX.toFixed(2)}, {vertexY.toFixed(2)})
          </span>
        )}
      </div>
    </div>
  );
}
