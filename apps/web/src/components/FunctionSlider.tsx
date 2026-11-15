import { useMemo, useState } from "react";
import type { Explain } from "../types";

const WIDTH = 420;
const HEIGHT = 260;
const PADDING = 30;
// 纵轴显示范围：固定值保证拖动滑块时坐标系不跳动
const Y_LIMIT = 8;
const STEPS = 180;

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

export default function FunctionSlider({ explain }: { explain: Explain }) {
  const { b, c, x_min, x_max } = explain.params;
  const [a, setA] = useState(explain.params.a);

  const toX = (x: number) =>
    PADDING + ((x - x_min) / (x_max - x_min)) * (WIDTH - PADDING * 2);
  const toY = (y: number) =>
    PADDING + ((Y_LIMIT - y) / (2 * Y_LIMIT)) * (HEIGHT - PADDING * 2);

  const path = useMemo(
    () => buildPath(a, b, c, x_min, x_max),
    [a, b, c, x_min, x_max]
  );

  const axisY = toY(0);
  const vertexX = a === 0 ? 0 : -b / (2 * a);
  const vertexY = a * vertexX * vertexX + b * vertexX + c;
  const vertexVisible =
    a !== 0 &&
    vertexX >= x_min &&
    vertexX <= x_max &&
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
          min={-3}
          max={3}
          step={0.1}
          value={a}
          onChange={(event) => setA(Number(event.target.value))}
        />
      </div>

      <div className="formula-line">
        y = {a.toFixed(1)}x² {b >= 0 ? "+" : "−"} {Math.abs(b)}x{" "}
        {c >= 0 ? "+" : "−"} {Math.abs(c)}
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
