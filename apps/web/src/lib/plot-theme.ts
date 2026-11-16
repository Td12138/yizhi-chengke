/**
 * 教材插图主题：函数图像（FunctionPlot）与二次函数滑块（FunctionSlider）共用。
 * 只放颜色、线宽、字号这类与具体图形无关的常量，
 * 画布尺寸与采样参数留在各自组件里。
 */

/* ---------- 纸墨丹青：墨色坐标轴 + 松烟绿曲线 + 朱砂红标记 ---------- */
export const COLOR_INK = "#3f3f46";
export const COLOR_CURVE = "#14532d";
export const COLOR_GRID = "#ece8df";
export const COLOR_TICK = "#71717a";
export const COLOR_PAPER = "#fffdf9";
export const COLOR_MARK = "#be123c";
/** 坐标轴字母 / 刻度沿用全站无衬线字体，Windows 上不发虚 */
export const AXIS_FONT = "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif";

export const AXIS_WIDTH = 1.2;
export const CURVE_WIDTH = 2;
export const GRID_WIDTH = 1;
/** 轴末端实心三角箭头：长、半宽 */
export const ARROW_LENGTH = 7;
export const ARROW_HALF_WIDTH = 3.5;

/** 图版最大显示宽度：面板再宽也不把刻度字放大成标题 */
export const FIGURE_MAX_WIDTH = 520;
/** 图版与页脚共用同一宽度并居中 */
export const FIGURE_STYLE = { maxWidth: FIGURE_MAX_WIDTH, margin: "0 auto" } as const;
