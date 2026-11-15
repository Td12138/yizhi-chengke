import { useMemo } from "react";

/**
 * 轻量函数图像渲染器：自带表达式解析（递归下降），不依赖 eval 与绘图库。
 * 支持 + - * / ^ ( )、一元负号、sin cos tan asin acos atan sqrt abs ln log exp、
 * 常量 pi / e、隐式乘法（2x、3(x+1)）。
 */

const WIDTH = 520;
const HEIGHT = 300;
const PADDING = 34;
const SAMPLES = 480;
// 超出该高度的点视为不在可视区，断开曲线而不是画一条竖线
const Y_CLAMP = 40;
// 纵轴自适应上限：再大就压成一条平线，不如裁掉两侧看形状
const Y_DISPLAY_MAX = 12;

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

type Props = {
  expression: string;
  xMin?: number;
  xMax?: number;
};

export default function FunctionPlot({ expression, xMin = -6, xMax = 6 }: Props) {
  const result = useMemo(() => {
    let fn: Fn;
    try {
      fn = parseExpression(expression);
    } catch (err) {
      if (err instanceof RangeError) {
        // 括号 / 幂次嵌套过深时解析器自身也可能爆栈
        return { error: "表达式嵌套过深，无法解析，请简化后再试" } as const;
      }
      return { error: err instanceof Error ? err.message : String(err) } as const;
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
        return { error: `${reason}，无法绘制，请简化后再试` } as const;
      }
      if (Number.isFinite(y)) points.push({ x, y });
    }
    if (points.length === 0) {
      return { error: "这个表达式在给定区间内没有有效取值" } as const;
    }

    const magnitudes = points.map((p) => Math.abs(p.y)).sort((a, b) => a - b);
    const p90 = magnitudes[Math.floor(magnitudes.length * 0.9)] || 1;
    const yLimit = Math.min(Y_DISPLAY_MAX, Y_CLAMP, Math.max(1, p90 * 1.25));
    return { points, yLimit } as const;
  }, [expression, xMin, xMax]);

  const toX = (x: number) => PADDING + ((x - xMin) / (xMax - xMin)) * (WIDTH - PADDING * 2);

  if ("error" in result) {
    return (
      <div className="plot">
        <p className="plot-error">{result.error}</p>
      </div>
    );
  }

  const { points, yLimit } = result;
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
  const tickStep = (xMax - xMin) / 12;

  return (
    <div className="plot">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`函数图像 ${expression}`}>
        {Array.from({ length: 13 }, (_, index) => {
          const x = xMin + tickStep * index;
          return (
            <line
              key={index}
              x1={toX(x)}
              y1={PADDING}
              x2={toX(x)}
              y2={HEIGHT - PADDING}
              className="plot-grid"
            />
          );
        })}
        {[-2, -1, 1, 2].map((factor) => (
          <line
            key={factor}
            x1={PADDING}
            y1={toY((yLimit * factor) / 2)}
            x2={WIDTH - PADDING}
            y2={toY((yLimit * factor) / 2)}
            className="plot-grid"
          />
        ))}
        <line x1={PADDING} y1={axisY} x2={WIDTH - PADDING} y2={axisY} className="plot-axis" />
        <line
          x1={toX(0)}
          y1={PADDING}
          x2={toX(0)}
          y2={HEIGHT - PADDING}
          className="plot-axis"
        />
        <path d={path} className="plot-curve" />
      </svg>
      <div className="plot-foot">
        <span>x ∈ [{xMin}, {xMax}]</span>
        <span>y ∈ [-{yLimit.toFixed(1)}, {yLimit.toFixed(1)}]</span>
      </div>
    </div>
  );
}
