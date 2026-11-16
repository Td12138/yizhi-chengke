import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { GraphData, GraphNode } from "../types";

/**
 * 知识点谱系图：手写力导向布局（斥力 + 弹簧 + 向心），
 * 迭代若干次后一次性渲染静态 SVG，不引入 d3 等图形库。
 */

/** 布局迭代次数：够收敛，40 个节点也只是一次渲染里的同步计算 */
const ITERATIONS = 250;
/** 库仑斥力系数：F = REPULSION / 距离²，越大节点越散 */
const REPULSION = 11000;
/** 弹簧系数与自然长度 */
const SPRING = 0.015;
const SPRING_LENGTH = 96;
/** 弹簧强度上限，避免高权重边把两端拉成一个点 */
const SPRING_CAP = 3;
/** 向心力系数：把孤立节点拉回画布中心 */
const CENTER_PULL = 0.004;
/** 单步位移上限，冷启动时防止节点被斥力炸飞 */
const MAX_STEP = 16;
/** 两节点最小间距，避免斥力除零，也给标签留出位置 */
const MIN_DISTANCE = 24;
/** 图形超过这个数量只画得分最高的前 N 个 */
const MAX_NODES = 40;
/** 常驻标签只给前 N 个节点，其余 hover 时才出现，避免压字 */
const LABEL_LIMIT = 8;
/** 节点文字最多保留几个字 */
const LABEL_CHARS = 9;
/** 节点散开的最大延迟；单节点动画本身 300ms（--motion-base） */
const GRAPH_SPREAD_DELAY_MS = 120;
/** 数字滚动时长，与 CSS 的 --motion-slow 对齐 */
const COUNT_UP_MS = 400;

/** 画布宽度兜底值；实际宽度由容器 ResizeObserver 量出来 */
const DEFAULT_WIDTH = 880;
/** 高度按节点数自适应：基数 + 每节点增量，封顶 560px */
const HEIGHT_BASE = 240;
const HEIGHT_PER_NODE = 18;
const HEIGHT_MAX = 560;
const PADDING = 48;
/** 布局结果的放大上限：只有两三个节点时别撑成巨无霸 */
const FIT_MAX = 1.8;
/** 布局结果的最小缩放，防止极端数据把点压成一坨 */
const FIT_MIN = 0.2;
/** 黄金角，用于确定性的初始螺旋分布 */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** 节点半径：出题越多越大，下限保证小节点不被标签压住 */
const RADIUS_MIN = 10;
const RADIUS_SCALE = 4.2;
const RADIUS_MAX = 28;

/* ---------- 学术脉络图配色：暖纸底 + 松烟绿 + 细实线 ---------- */
/** 没出过题也没被问过的中性纸色 */
const COLOR_NEUTRAL_FILL = "#efe9dc";
const COLOR_NEUTRAL_STROKE = "#9c917f";
/** 有题未问的浅松烟绿（#dce8e3）；被问深度递增时向主色（#1b4d3e）过渡 */
const COLOR_LOW: [number, number, number] = [220, 232, 227];
const COLOR_ACCENT: [number, number, number] = [27, 77, 62];
/** 深主色描边（#0f342a），让重色节点轮廓更实 */
const COLOR_ACCENT_DARK: [number, number, number] = [15, 52, 42];
const COLOR_FRESH_FILL = "#dce8e3";
const COLOR_FRESH_STROKE = "#1b4d3e";
const COLOR_TEXT = "#2e2a23";
const COLOR_TEXT_LIGHT = "#fbf8f1";
/** 连线：默认浅褐，高亮松烟绿 */
const COLOR_EDGE = "#9c917f";
const COLOR_EDGE_ACTIVE = "#1b4d3e";
/** 图版编号 / 轴注用衬线字 */
const SERIF = "Georgia, 'Times New Roman', serif";

/** 节点内圈（细实线双圈）：内缩距离、线宽、透明度 */
const INNER_RING_INSET = 3.5;
const INNER_RING_WIDTH = 0.8;
const INNER_RING_OPACITY = 0.55;
/** 半径够大才在节点中央写出题数 / 问次徽标 */
const BADGE_MIN_RADIUS = 15;
const BADGE_FONT_SIZE = 10;
/** 徽标数字基线相对圆心下移的比例（视觉居中） */
const BADGE_BASELINE_RATIO = 0.35;
/** 填充深到这个程度时徽标改用纸色 */
const BADGE_LIGHT_TEXT_DEPTH = 0.45;
/** 选中 / 悬停时无关节点降到这个透明度 */
const NODE_DIM_OPACITY = 0.45;

/** 连线宽度、透明度与高亮加粗量 */
const EDGE_WIDTH_BASE = 0.7;
const EDGE_WIDTH_SCALE = 2.4;
const EDGE_OPACITY_BASE = 0.14;
const EDGE_OPACITY_SCALE = 0.5;
const EDGE_DIM_FACTOR = 0.28;
const EDGE_ACTIVE_OPACITY = 0.85;
const EDGE_ACTIVE_BOOST = 1.2;

/**
 * 左上角统计行：沿用 .graph-note 的等宽小字，只去掉外框与底色，
 * 读起来像图版的编号说明而不是状态药丸。
 * 定位（top / left / right）全部交给 .graph-note-left，不在组件里写死。
 */
const CAPTION_STYLE: CSSProperties = {
  position: "absolute",
  padding: 0,
  border: "none",
  background: "transparent",
  borderRadius: 0,
  fontVariantNumeric: "tabular-nums",
  pointerEvents: "none",
};

type Props = {
  graph: GraphData | null;
  loading: boolean;
  error: string;
};

type PlacedNode = {
  node: GraphNode;
  x: number;
  y: number;
  r: number;
  /** 被问次数归一化后的深浅，0 = 没被问过 */
  depth: number;
  /** 入场动画延迟（按离中心距离分配） */
  delay: number;
};

type PlacedEdge = {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  source: number;
  target: number;
  /** 相对最大权重的强度，0–1 */
  strength: number;
};

type Layout = {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  labelIds: number[];
  total: number;
  truncated: boolean;
};

type SimNode = { x: number; y: number };

/** 把两个 RGB 颜色按 t∈[0,1] 混合 */
function mix(from: [number, number, number], to: [number, number, number], t: number): string {
  const p = Math.min(1, Math.max(0, t));
  const r = Math.round(from[0] + (to[0] - from[0]) * p);
  const g = Math.round(from[1] + (to[1] - from[1]) * p);
  const b = Math.round(from[2] + (to[2] - from[2]) * p);
  return `rgb(${r}, ${g}, ${b})`;
}

const EMPTY_LAYOUT: Layout = {
  nodes: [],
  edges: [],
  labelIds: [],
  total: 0,
  truncated: false,
};

function score(node: GraphNode): number {
  return (node.ask_count ?? 0) + (node.question_count ?? 0);
}

function truncate(text: string): string {
  return text.length > LABEL_CHARS ? `${text.slice(0, LABEL_CHARS)}…` : text;
}

function buildLayout(graph: GraphData | null, width: number, height: number): Layout {
  const source = graph?.nodes ?? [];
  if (source.length === 0) return EMPTY_LAYOUT;

  const total = source.length;
  // 超过上限只画前 40 个：按「被问 + 出题」总量排序
  const picked = [...source].sort((a, b) => score(b) - score(a)).slice(0, MAX_NODES);
  const truncated = total > MAX_NODES;
  const idSet = new Set(picked.map((node) => node.id));
  const labelIds = picked.slice(0, LABEL_LIMIT).map((node) => node.id);

  const count = picked.length;
  const maxAsk = Math.max(1, ...picked.map((node) => node.ask_count ?? 0));
  const radii = picked.map((node) =>
    Math.min(
      RADIUS_MAX,
      RADIUS_MIN + Math.sqrt(Math.max(0, node.question_count ?? 0)) * RADIUS_SCALE
    )
  );

  // 确定性初值：黄金角螺旋，同一份数据每次画出来都一样
  const spawn = Math.min(width, height) * 0.3;
  const sim: SimNode[] = picked.map((_, index) => {
    const angle = index * GOLDEN_ANGLE;
    const radius = Math.sqrt((index + 0.5) / count) * spawn;
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
  const offsets = new Map<number, number>();
  picked.forEach((node, index) => offsets.set(node.id, index));

  const links = (graph?.edges ?? [])
    .filter(
      (edge) =>
        idSet.has(edge.source) &&
        idSet.has(edge.target) &&
        edge.source !== edge.target &&
        offsets.has(edge.source) &&
        offsets.has(edge.target)
    )
    .map((edge) => ({
      a: offsets.get(edge.source) as number,
      b: offsets.get(edge.target) as number,
      weight: Math.max(0, Number(edge.weight) || 0),
    }));
  const maxWeight = links.reduce((max, link) => Math.max(max, link.weight), 0);

  for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
    // 退火：越到后面步子越小，最后稳定下来
    const cooling = 1 - iteration / ITERATIONS;
    const forceX = new Array<number>(count).fill(0);
    const forceY = new Array<number>(count).fill(0);

    // 斥力：所有节点两两相推
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let dx = sim[i].x - sim[j].x;
        let dy = sim[i].y - sim[j].y;
        let distance = Math.hypot(dx, dy);
        if (distance < MIN_DISTANCE) {
          // 重合时按索引给一个确定性的错开方向
          const angle = ((i + 1) * 0.7 + (j + 1) * 1.3) % (Math.PI * 2);
          dx = Math.cos(angle) * MIN_DISTANCE;
          dy = Math.sin(angle) * MIN_DISTANCE;
          distance = MIN_DISTANCE;
        }
        const push = REPULSION / (distance * distance);
        const ux = dx / distance;
        const uy = dy / distance;
        forceX[i] += ux * push;
        forceY[i] += uy * push;
        forceX[j] -= ux * push;
        forceY[j] -= uy * push;
      }
    }

    // 弹簧：有共同出现的节点互相拉近，权重越大拉得越紧
    for (const link of links) {
      const dx = sim[link.b].x - sim[link.a].x;
      const dy = sim[link.b].y - sim[link.a].y;
      const distance = Math.hypot(dx, dy) || 1;
      const stiffness = Math.min(SPRING_CAP, 1 + Math.log2(1 + link.weight) * 0.5);
      const pull = (distance - SPRING_LENGTH / stiffness) * SPRING * stiffness;
      const ux = dx / distance;
      const uy = dy / distance;
      forceX[link.a] += ux * pull;
      forceY[link.a] += uy * pull;
      forceX[link.b] -= ux * pull;
      forceY[link.b] -= uy * pull;
    }

    // 向心 + 限幅：大节点惯性更大，位移按半径折算
    for (let i = 0; i < count; i += 1) {
      forceX[i] -= sim[i].x * CENTER_PULL;
      forceY[i] -= sim[i].y * CENTER_PULL;
      const limit = MAX_STEP * cooling;
      const mass = 1 + radii[i] / RADIUS_MAX;
      sim[i].x += Math.max(-limit, Math.min(limit, forceX[i])) / mass;
      sim[i].y += Math.max(-limit, Math.min(limit, forceY[i])) / mass;
    }
  }

  // 把模拟坐标缩放到画布内：横纵分别适配，宽画布也能铺开，不留大片空白
  const xs = sim.map((point) => point.x);
  const ys = sim.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const fitX = Math.min(
    FIT_MAX,
    Math.max(FIT_MIN, (width - PADDING * 2) / spanX)
  );
  const fitY = Math.min(
    FIT_MAX,
    Math.max(FIT_MIN, (height - PADDING * 2) / spanY)
  );
  const midX = (minX + maxX) / 2;
  const midY = (minY + maxY) / 2;

  const nodes: PlacedNode[] = picked
    .map((node, index) => ({
      node,
      x: width / 2 + (sim[index].x - midX) * fitX,
      y: height / 2 + (sim[index].y - midY) * fitY,
      r: radii[index],
      depth:
        (node.ask_count ?? 0) > 0
          ? Math.sqrt(Math.min(1, (node.ask_count ?? 0) / maxAsk))
          : 0,
      delay: 0,
    }))
    // 小的先画、大的压在上面，避免小节点被盖住
    .sort((a, b) => a.r - b.r);

  // 离容器中心越远，入场越晚（总延迟不超过 GRAPH_SPREAD_DELAY_MS）
  const centerX = width / 2;
  const centerY = height / 2;
  const distances = nodes.map((item) =>
    Math.hypot(item.x - centerX, item.y - centerY)
  );
  const maxDistance = Math.max(1, ...distances);
  nodes.forEach((item, index) => {
    item.delay = Math.round((distances[index] / maxDistance) * GRAPH_SPREAD_DELAY_MS);
  });

  const placed = new Map<number, PlacedNode>();
  for (const item of nodes) placed.set(item.node.id, item);

  const edges: PlacedEdge[] = [];
  for (const [index, link] of links.entries()) {
    const from = placed.get(picked[link.a].id);
    const to = placed.get(picked[link.b].id);
    if (!from || !to) continue;
    edges.push({
      key: `e-${index}-${from.node.id}-${to.node.id}`,
      x1: from.x,
      y1: from.y,
      x2: to.x,
      y2: to.y,
      source: from.node.id,
      target: to.node.id,
      strength: maxWeight > 0 ? link.weight / maxWeight : 0.5,
    });
  }

  return { nodes, edges, labelIds, total, truncated };
}

/** 系统是否要求减少动态效果 */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 计数滚动：数值变化时在 COUNT_UP_MS 内从旧值递增到新值。
 * 只有数字变化才会动画，首次挂载直接显示目标值。
 */
function CountUp({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const target = useRef(value);

  useEffect(() => {
    const from = target.current;
    if (from === value) return;
    target.current = value;

    if (prefersReducedMotion()) {
      setShown(value);
      return;
    }

    const startedAt = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / COUNT_UP_MS);
      // easeOutCubic：先快后慢，读数不跳
      const eased = 1 - Math.pow(1 - progress, 3);
      setShown(Math.round(from + (value - from) * eased));
      if (progress < 1) raf = window.requestAnimationFrame(step);
    };
    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [value]);

  return <>{shown}</>;
}

export default function KnowledgeGraph({ graph, loading, error }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [hoverId, setHoverId] = useState<number | null>(null);

  /** 容器实测宽度：viewBox 与显示尺寸 1:1，圆形不被拉扁、tooltip 定位也准 */
  const [wrap, setWrap] = useState<HTMLDivElement | null>(null);
  const [canvasWidth, setCanvasWidth] = useState(DEFAULT_WIDTH);
  const frame = useRef(0);

  useEffect(() => {
    if (!wrap) return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.max(320, Math.round(entries[0].contentRect.width));
      window.cancelAnimationFrame(frame.current);
      frame.current = window.requestAnimationFrame(() => {
        setCanvasWidth((prev) => (Math.abs(prev - next) >= 8 ? next : prev));
      });
    });
    observer.observe(wrap);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame.current);
    };
  }, [wrap]);

  // 高度随节点数增长，上限 560px；节点少时不留大片空白
  const shownCount = Math.min(MAX_NODES, graph?.nodes.length ?? 0);
  const canvasHeight = Math.min(
    HEIGHT_MAX,
    HEIGHT_BASE + shownCount * HEIGHT_PER_NODE
  );

  const layout = useMemo(
    () => buildLayout(graph, canvasWidth, canvasHeight),
    [graph, canvasWidth, canvasHeight]
  );
  const byId = useMemo(
    () => new Map(layout.nodes.map((item) => [item.node.id, item])),
    [layout]
  );

  const selected = selectedId === null ? null : byId.get(selectedId) ?? null;
  const hovered = hoverId === null ? null : byId.get(hoverId) ?? null;

  /** 当前聚焦的节点：选中优先，其次悬停；聚焦时邻居保持清晰、无关节点淡出 */
  const focusId = selected?.node.id ?? hovered?.node.id ?? null;

  const focusEdges = useMemo(() => {
    if (focusId === null) return null;
    return new Set(
      layout.edges
        .filter((edge) => edge.source === focusId || edge.target === focusId)
        .map((edge) => edge.key)
    );
  }, [focusId, layout.edges]);

  const focusNodes = useMemo(() => {
    if (focusId === null) return null;
    const ids = new Set<number>([focusId]);
    for (const edge of layout.edges) {
      if (edge.source === focusId) ids.add(edge.target);
      else if (edge.target === focusId) ids.add(edge.source);
    }
    return ids;
  }, [focusId, layout.edges]);

  const toggle = (id: number) =>
    setSelectedId((prev) => (prev === id ? null : id));

  if (error) {
    return (
      <div className="graph-empty">
        <p>读取知识谱系失败：{error}</p>
        <p className="graph-empty-sub">点本面板右上角「刷新」可以重试。</p>
      </div>
    );
  }

  if (!graph || (loading && layout.nodes.length === 0)) {
    return <div className="graph-empty">正在加载知识谱系…</div>;
  }

  if (layout.nodes.length === 0) {
    return (
      <div className="graph-empty">
        <p>还没有知识点，谱系图是空的。</p>
        <p className="graph-empty-sub">
          先在第 1 栏上传一页板书或讲义，卡片入库后这里会自动画出知识点之间的关系。
        </p>
      </div>
    );
  }

  /** 节点贴顶时气泡改到下方，并从节点半径外开始，别压住节点标签 */
  const tipBelow = hovered ? hovered.y < 70 : false;
  const tipStyle: CSSProperties | undefined = hovered
    ? {
        left: `${Math.min(88, Math.max(12, (hovered.x / canvasWidth) * 100))}%`,
        top: `${(hovered.y / canvasHeight) * 100}%`,
        transform: tipBelow
          ? `translate(-50%, ${Math.round(hovered.r + 20)}px)`
          : undefined,
      }
    : undefined;

  return (
    <div className="graph">
      <div className="graph-wrap" ref={setWrap} style={{ height: canvasHeight }}>
        {/* 图版说明：截断时把「显示多少 / 共多少」一起写进这一行，只留左上角一处 */}
        <span
          className="graph-note graph-note-left"
          style={CAPTION_STYLE}
        >
          {layout.truncated
            ? `Fig. 知识脉络 · 显示 ${layout.nodes.length}/${layout.total} 节点 · ${layout.edges.length} 关联`
            : `Fig. 知识脉络 · ${layout.total} 节点 / ${layout.edges.length} 关联`}
        </span>

        <svg
          className="graph-svg"
          viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
          role="img"
          aria-label={`知识点谱系图，共 ${layout.total} 个知识点`}
        >
          <g>
            {layout.edges.map((edge) => {
              const active = focusEdges?.has(edge.key) ?? false;
              const dim = focusEdges !== null && !active;
              return (
                <line
                  key={edge.key}
                  className="graph-edge"
                  x1={edge.x1}
                  y1={edge.y1}
                  x2={edge.x2}
                  y2={edge.y2}
                  stroke={active ? COLOR_EDGE_ACTIVE : COLOR_EDGE}
                  strokeWidth={
                    EDGE_WIDTH_BASE +
                    edge.strength * EDGE_WIDTH_SCALE +
                    (active ? EDGE_ACTIVE_BOOST : 0)
                  }
                  strokeOpacity={
                    active
                      ? EDGE_ACTIVE_OPACITY
                      : (EDGE_OPACITY_BASE + edge.strength * EDGE_OPACITY_SCALE) *
                        (dim ? EDGE_DIM_FACTOR : 1)
                  }
                  strokeLinecap="round"
                />
              );
            })}
          </g>
          <g>
            {layout.nodes.map((item) => {
              const { node } = item;
              const isSelected = selected?.node.id === node.id;
              const isHovered = hovered?.node.id === node.id;
              const ask = node.ask_count ?? 0;
              const questions = node.question_count ?? 0;
              const hasAsk = ask > 0;
              const hasQuestions = questions > 0;
              // 有题未问给浅松烟绿，完全没进过流程才用中性纸色
              const fill = hasAsk
                ? mix(COLOR_LOW, COLOR_ACCENT, 0.2 + item.depth * 0.8)
                : hasQuestions
                  ? COLOR_FRESH_FILL
                  : COLOR_NEUTRAL_FILL;
              const stroke = hasAsk
                ? mix(COLOR_ACCENT, COLOR_ACCENT_DARK, item.depth)
                : hasQuestions
                  ? COLOR_FRESH_STROKE
                  : COLOR_NEUTRAL_STROKE;
              // 聚焦时无关节点淡出，邻居保持清晰
              const dimmed = focusNodes !== null && !focusNodes.has(node.id);
              const label = layout.labelIds.includes(node.id) || isSelected || isHovered;
              // 半径够大才在圈内写数字：被问过写问次，否则写出题数
              const badge =
                item.r >= BADGE_MIN_RADIUS ? (hasAsk ? ask : questions) : 0;
              const badgeFill =
                hasAsk && item.depth > BADGE_LIGHT_TEXT_DEPTH
                  ? COLOR_TEXT_LIGHT
                  : COLOR_TEXT;
              return (
                <g
                  key={node.id}
                  transform={`translate(${item.x.toFixed(1)} ${item.y.toFixed(1)})`}
                  style={{
                    opacity: dimmed ? NODE_DIM_OPACITY : 1,
                    transition: "opacity var(--motion-fast) ease",
                  }}
                >
                  <g
                    className={`graph-node graph-node-in ${
                      isSelected ? "graph-node-on" : ""
                    }`}
                    style={
                      {
                        animationDelay: `${item.delay}ms`,
                        "--from-x": `${Math.round(canvasWidth / 2 - item.x)}px`,
                        "--from-y": `${Math.round(canvasHeight / 2 - item.y)}px`,
                      } as CSSProperties
                    }
                    role="button"
                    tabIndex={0}
                    aria-label={`${node.title}，被问 ${ask} 次，出题 ${questions} 道`}
                    onClick={() => toggle(node.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggle(node.id);
                      }
                    }}
                    onMouseEnter={() => setHoverId(node.id)}
                    onMouseLeave={() => setHoverId(null)}
                    onFocus={() => setHoverId(node.id)}
                    onBlur={() => setHoverId(null)}
                  >
                    <title>{`${node.title} · 被问 ${ask} 次 · 出题 ${questions} 道`}</title>
                    {isSelected && (
                      <circle
                        r={item.r + 4}
                        fill="none"
                        stroke={COLOR_EDGE_ACTIVE}
                        strokeWidth={1}
                        strokeDasharray="3 3"
                      />
                    )}
                    <circle
                      className="graph-node-dot"
                      r={item.r}
                      fill={fill}
                      stroke={isHovered || isSelected ? COLOR_TEXT : stroke}
                      strokeWidth={isHovered || isSelected ? 1.8 : 1.2}
                    />
                    {badge > 0 && (
                      <>
                        {/* 细实线内圈：学术图谱的「双圈」观感，不发光 */}
                        <circle
                          className="graph-node-dot"
                          r={item.r - INNER_RING_INSET}
                          fill="none"
                          stroke={stroke}
                          strokeWidth={INNER_RING_WIDTH}
                          strokeOpacity={INNER_RING_OPACITY}
                          pointerEvents="none"
                        />
                        <text
                          y={BADGE_FONT_SIZE * BADGE_BASELINE_RATIO}
                          textAnchor="middle"
                          fontSize={BADGE_FONT_SIZE}
                          fontFamily={SERIF}
                          fill={badgeFill}
                          pointerEvents="none"
                        >
                          {badge}
                        </text>
                      </>
                    )}
                    {label && (
                      <text
                        className="graph-label"
                        y={item.r + 13}
                        textAnchor="middle"
                        fill={COLOR_TEXT}
                      >
                        {truncate(node.title)}
                      </text>
                    )}
                  </g>
                </g>
              );
            })}
          </g>
        </svg>
        {hovered && (
          <div className="graph-tip" style={tipStyle}>
            <strong>{hovered.node.title}</strong>
            <span>
              被问 <CountUp value={hovered.node.ask_count ?? 0} /> 次 · 出题{" "}
              <CountUp value={hovered.node.question_count ?? 0} /> 道
            </span>
          </div>
        )}
        <span className="graph-legend">
          节点大小 = 出题数 · 浅绿 = 有题未问 · 深绿 = 被问次数 · 连线 = 共享标签
        </span>
      </div>

      {selected ? (
        <div className="graph-detail">
          <div className="graph-detail-head">
            <strong>{selected.node.title}</strong>
            {selected.node.subject && <span className="tag">{selected.node.subject}</span>}
          </div>
          <p>{selected.node.summary || "这张卡片还没有摘要。"}</p>
          <div className="graph-detail-meta">
            <span className="tag">
              被问 <CountUp value={selected.node.ask_count ?? 0} /> 次
            </span>
            <span className="tag">
              出题 <CountUp value={selected.node.question_count ?? 0} /> 道
            </span>
            {(selected.node.tags ?? []).map((tag) => (
              <span key={tag} className="tag">
                {tag}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div className="graph-detail graph-detail-empty">
          点一个知识点，这里显示它的摘要、标签与被问次数。
        </div>
      )}
    </div>
  );
}
