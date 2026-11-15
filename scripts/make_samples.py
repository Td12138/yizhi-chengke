"""生成三张演示板书样例图（纯 PIL 绘制，无外网素材、无真实个人资料）。

用法：
    <python> scripts/make_samples.py

输出：
    apps/web/public/samples/sample-quadratic.png
    apps/web/public/samples/sample-equation.png
    apps/web/public/samples/sample-trig.png

说明：样例图放在前端 public 目录，前端可直接以 /samples/xxx.png 取用，
「用示例板书试一试」按钮即依赖这三个文件。

约定：
- 路径一律由脚本自身位置推导，不写死盘符；
- 中文字体优先 C:/Windows/Fonts/msyh.ttc，找不到任何中文字体时
  自动退化为英文版并在终端打印说明（图仍可生成，用于流程演示）。
"""

from __future__ import annotations

import math
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- 路径与常量

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "apps" / "web" / "public" / "samples"

CANVAS_SIZE = (1600, 1200)
MARGIN = 92
BODY_SIZE = 44
SMALL_SIZE = 30
TITLE_SIZE = 68
LABEL_SIZE = 26

BOARD_COLOR = (26, 47, 39)
CHALK = (240, 241, 233)
CHALK_DIM = (178, 201, 187)
ACCENT = (247, 214, 122)
GRAPH_COLOR = (146, 214, 189)
GRAPH_ALT = (222, 168, 132)

NOISE_SEED = 20261115
NOISE_POINTS = 9000
NOISE_ALPHA = 16

# 中文字体候选：Windows 字体目录由系统变量推导（不写死盘符），
# 其余为 macOS / Linux 常见路径；全部缺失时走英文版
_WINDIR = Path(os.environ.get("WINDIR", "C:/Windows"))
CJK_FONT_CANDIDATES = (
    str(_WINDIR / "Fonts" / "msyh.ttc"),
    str(_WINDIR / "Fonts" / "msyh.ttf"),
    str(_WINDIR / "Fonts" / "simhei.ttf"),
    str(_WINDIR / "Fonts" / "simsun.ttc"),
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def resolve_font() -> tuple[str | None, bool]:
    """返回 (可用字体路径, 是否支持中文)。"""
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            try:
                ImageFont.truetype(str(path), 20)
            except OSError:
                continue
            return str(path), True
    return None, False


FONT_PATH, HAS_CJK = resolve_font()

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _FONT_CACHE:
        if FONT_PATH:
            _FONT_CACHE[size] = ImageFont.truetype(FONT_PATH, size)
        else:
            _FONT_CACHE[size] = ImageFont.load_default(size=size)
    return _FONT_CACHE[size]


# ---------------------------------------------------------------- 画板封装


class Board:
    """一块深绿色板书：标题、正文行、手绘坐标轴。"""

    def __init__(self, cjk: bool) -> None:
        self.cjk = cjk
        self.image = Image.new("RGB", CANVAS_SIZE, BOARD_COLOR)
        self.draw = ImageDraw.Draw(self.image)
        self.cursor = MARGIN
        self.clip_box: tuple[int, int, int, int] | None = None

    # -- 文案：自动选中文或英文
    def t(self, zh: str, en: str) -> str:
        return zh if self.cjk else en

    # -- 基础元素
    def title(self, text: str, note: str = "") -> None:
        self.draw.text((MARGIN, self.cursor), text, font=font(TITLE_SIZE), fill=ACCENT)
        if note:
            width = self.draw.textlength(text, font=font(TITLE_SIZE))
            self.draw.text(
                (MARGIN + width + 28, self.cursor + 34),
                note,
                font=font(SMALL_SIZE),
                fill=CHALK_DIM,
            )
        self.cursor += TITLE_SIZE + 16
        self.draw.line(
            [(MARGIN, self.cursor + 12), (CANVAS_SIZE[0] - MARGIN, self.cursor + 12)],
            fill=CHALK_DIM,
            width=3,
        )
        self.cursor += 52

    def line(
        self,
        text: str,
        size: int = BODY_SIZE,
        color: tuple[int, int, int] = CHALK,
        indent: int = 0,
        gap: int = 0,
    ) -> None:
        x = MARGIN + indent
        draw_size = size
        max_width = CANVAS_SIZE[0] - MARGIN - x
        while draw_size > 24 and self.draw.textlength(text, font=font(draw_size)) > max_width:
            draw_size -= 2
        self.draw.text((x, self.cursor), text, font=font(draw_size), fill=color)
        self.cursor += (gap or int(draw_size * 1.62))

    def blank(self, height: int = 24) -> None:
        self.cursor += height

    # -- 坐标系
    def axes(
        self,
        box: tuple[int, int, int, int],
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> tuple[callable, callable]:
        x0, y0, x1, y1 = box
        (xmin, xmax), (ymin, ymax) = x_range, y_range

        def px(value: float) -> float:
            return x0 + (value - xmin) / (xmax - xmin) * (x1 - x0)

        def py(value: float) -> float:
            return y1 - (value - ymin) / (ymax - ymin) * (y1 - y0)

        self.draw.rectangle(box, outline=CHALK_DIM, width=2)
        if ymin <= 0 <= ymax:
            self.draw.line([(x0, py(0)), (x1, py(0))], fill=CHALK_DIM, width=2)
        if xmin <= 0 <= xmax:
            self.draw.line([(px(0), y0), (px(0), y1)], fill=CHALK_DIM, width=2)
        self.clip_box = box
        return px, py

    def curve(
        self,
        px: callable,
        py: callable,
        fn: callable,
        lo: float,
        hi: float,
        color: tuple[int, int, int] = GRAPH_COLOR,
        width: int = 4,
        steps: int = 320,
    ) -> None:
        """采样折线；超出坐标系窗口的部分裁掉，避免压到正文。"""
        left, top, right, bottom = self.clip_box or (
            MARGIN,
            MARGIN,
            CANVAS_SIZE[0] - MARGIN,
            CANVAS_SIZE[1] - MARGIN,
        )
        segment: list[tuple[float, float]] = []

        def flush() -> None:
            if len(segment) > 1:
                self.draw.line(segment, fill=color, width=width, joint="curve")
            segment.clear()

        for index in range(steps + 1):
            xv = lo + (hi - lo) * index / steps
            try:
                yv = fn(xv)
            except (ValueError, ZeroDivisionError):
                flush()
                continue
            sx, sy = px(xv), py(yv)
            if left <= sx <= right and top <= sy <= bottom:
                segment.append((sx, sy))
            else:
                flush()
        flush()

    def dot(
        self,
        x: float,
        y: float,
        color: tuple[int, int, int] = ACCENT,
        radius: int = 7,
    ) -> None:
        self.draw.ellipse(
            [(x - radius, y - radius), (x + radius, y + radius)], fill=color
        )

    def tag(
        self,
        x: float,
        y: float,
        text: str,
        color: tuple[int, int, int] = CHALK,
        dx: int = 12,
        dy: int = -34,
    ) -> None:
        self.draw.text((x + dx, y + dy), text, font=font(LABEL_SIZE), fill=color)

    # -- 收尾：噪点 + 暗角，模拟实拍板面
    def finish(self) -> Image.Image:
        grain = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        pen = ImageDraw.Draw(grain)
        rng = random.Random(NOISE_SEED)
        width, height = CANVAS_SIZE
        for _ in range(NOISE_POINTS):
            gx = rng.randrange(width)
            gy = rng.randrange(height)
            shade = rng.randrange(150, 255)
            pen.point((gx, gy), fill=(shade, shade, shade, NOISE_ALPHA))

        image = Image.alpha_composite(self.image.convert("RGBA"), grain).convert("RGB")

        vignette = Image.new("L", (64, 48))
        vx, vy = 32.0, 24.0
        for gy in range(48):
            for gx in range(64):
                dist = math.hypot((gx - vx) / vx, (gy - vy) / vy)
                value = int(max(0.0, 1.0 - 0.42 * dist**2) * 255)
                vignette.putpixel((gx, gy), value)
        mask = vignette.resize(CANVAS_SIZE, Image.BILINEAR).filter(
            ImageFilter.GaussianBlur(8)
        )
        return Image.composite(image, Image.new("RGB", CANVAS_SIZE, (8, 16, 13)), mask)


def stamp(board: Board, index: int) -> None:
    """右上角写样例编号，方便演示时一眼认出这是第几张。"""
    text = board.t(f"板书样例 {index:02d} / 03", f"Sample {index:02d} / 03")
    width = board.draw.textlength(text, font=font(SMALL_SIZE))
    board.draw.text(
        (CANVAS_SIZE[0] - MARGIN - width, 40),
        text,
        font=font(SMALL_SIZE),
        fill=CHALK_DIM,
    )


# ---------------------------------------------------------------- 三张样例


def make_quadratic(cjk: bool) -> Image.Image:
    """① 二次函数图象与性质。"""
    board = Board(cjk)
    board.title(
        board.t("二次函数的图象与性质", "Quadratic Functions: Graph and Properties"),
        board.t("九年级 · 函数", "Grade 9"),
    )
    board.line(board.t("一般式：y = ax² + bx + c   （a ≠ 0）",
                       "General form:  y = ax^2 + bx + c   (a != 0)"))
    board.line(board.t("配方得顶点式：y = a(x + b/2a)² + (4ac − b²) / 4a",
                       "Vertex form:  y = a(x + b/2a)^2 + (4ac - b^2) / 4a"))
    board.line(board.t("顶点 (−b/2a, (4ac − b²)/4a)，对称轴 x = −b/2a",
                       "Vertex (-b/2a, (4ac - b^2)/4a),  axis x = -b/2a"))
    board.line(board.t("开口：a > 0 向上，a < 0 向下；|a| 越大开口越窄",
                       "Opening: a > 0 up, a < 0 down;  larger |a| means narrower"))
    board.line(board.t("判别式 Δ = b² − 4ac：Δ>0 两个零点，Δ=0 一个，Δ<0 无实零点",
                       "Discriminant: D > 0 two roots, D = 0 one, D < 0 none"))
    board.blank(10)

    box = (MARGIN, board.cursor, 780, 1118)
    px, py = board.axes(box, (-1.0, 5.0), (-2.6, 5.2))
    board.curve(px, py, lambda x: x * x - 4 * x + 3, -1.0, 5.0)

    # 对称轴（虚线）
    for step in range(0, 34, 2):
        board.draw.line(
            [(px(2), box[1] + step * 12), (px(2), box[1] + step * 12 + 7)],
            fill=CHALK_DIM,
            width=2,
        )
    board.dot(px(2), py(-1))
    board.tag(px(2), py(-1), board.t("顶点 (2, −1)", "vertex (2, -1)"), CHALK, dx=-64, dy=28)
    for root in (1.0, 3.0):
        board.dot(px(root), py(0), CHALK, 6)
        board.tag(px(root), py(0), f"x={root:g}", CHALK, dx=-30, dy=16)
    board.tag(px(4.9), py(0), "x", CHALK, dx=-10, dy=10)
    board.tag(px(0), py(4.7), "y", CHALK, dx=-34, dy=-34)
    board.tag(px(2), py(4.2), board.t("对称轴", "axis"), CHALK_DIM, dx=-30)

    # 右侧例题
    text_x = 856
    original = board.cursor
    board.cursor = 700
    for text, color, size in (
        (board.t("例：y = x² − 4x + 3", "Example:  y = x^2 - 4x + 3"), CHALK, 46),
        (board.t("     = (x − 2)² − 1", "     = (x - 2)^2 - 1"), CHALK, 46),
        (board.t("顶点 (2, −1)", "vertex (2, -1)"), CHALK_DIM, 38),
        (board.t("零点 x = 1 与 x = 3", "roots x = 1 and x = 3"), CHALK_DIM, 38),
        (board.t("对称轴 x = 2", "axis x = 2"), CHALK_DIM, 38),
        (board.t("a = 1 > 0 → 开口向上", "a = 1 > 0 -> opens up"), CHALK_DIM, 38),
    ):
        board.draw.text((text_x, board.cursor), text, font=font(size), fill=color)
        board.cursor += int(size * 1.75)
    board.cursor = original

    stamp(board, 1)
    return board.finish()


def make_root_formula(cjk: bool) -> Image.Image:
    """② 一元二次方程求根。"""
    board = Board(cjk)
    board.title(
        board.t("一元二次方程的求根", "Solving Quadratic Equations"),
        board.t("九年级 · 代数", "Grade 9"),
    )
    board.line(board.t("标准形式：ax² + bx + c = 0   （a ≠ 0）",
                       "Standard form:  ax^2 + bx + c = 0   (a != 0)"))

    formula = board.t("求根公式：x = (−b ± √(b² − 4ac)) / (2a)",
                      "Formula:  x = (-b +/- sqrt(b^2 - 4ac)) / (2a)")
    board.line(formula, size=46, color=ACCENT)

    board.line(board.t("判别式 Δ = b² − 4ac 决定根的个数：",
                       "Discriminant D = b^2 - 4ac decides the number of roots:"))
    board.line(board.t("Δ > 0 → 两个不相等实根", "D > 0  ->  two distinct real roots"),
               size=38, color=CHALK_DIM, indent=48, gap=60)
    board.line(board.t("Δ = 0 → 两个相等实根 x = −b/2a",
                       "D = 0  ->  one repeated root x = -b/2a"),
               size=38, color=CHALK_DIM, indent=48, gap=60)
    board.line(board.t("Δ < 0 → 无实数根（复数范围内有两个根）",
                       "D < 0  ->  no real root (two complex roots)"),
               size=38, color=CHALK_DIM, indent=48, gap=60)

    board.blank(6)
    board.line(board.t("例：x² − 5x + 6 = 0", "Example:  x^2 - 5x + 6 = 0"), size=44)
    board.line(board.t("因式分解 (x − 2)(x − 3) = 0 → x1 = 2，x2 = 3",
                       "Factor:  (x - 2)(x - 3) = 0  ->  x1 = 2, x2 = 3"),
               size=40, gap=62)
    board.line(board.t("公式代入 x = (5 ± √(25 − 24)) / 2 = (5 ± 1) / 2 → 2 或 3",
                       "By formula: x = (5 +/- 1) / 2  ->  2 or 3"),
               size=40, gap=62)
    board.line(board.t("韦达定理：x1 + x2 = −b/a，x1x2 = c/a",
                       "Vieta:  x1 + x2 = -b/a,  x1 * x2 = c/a"),
               size=42, color=CHALK_DIM)
    board.blank(16)
    board.line(board.t("练习：2x² − 7x + 3 = 0（先算 Δ = 25）",
                       "Try:  2x^2 - 7x + 3 = 0   (start with D = 25)"),
               size=40, color=CHALK_DIM)

    # 底部数轴：把两个根标出来
    axis_y = 1082
    left, right = 220, 1400
    board.draw.line([(left, axis_y), (right, axis_y)], fill=CHALK_DIM, width=3)
    board.draw.polygon(
        [(right, axis_y), (right - 18, axis_y - 8), (right - 18, axis_y + 8)],
        fill=CHALK_DIM,
    )
    for value in range(0, 7):
        xpos = left + (right - left) * value / 6
        board.draw.line([(xpos, axis_y - 7), (xpos, axis_y + 7)], fill=CHALK_DIM, width=2)
        board.draw.text(
            (xpos - 8, axis_y + 14), str(value), font=font(LABEL_SIZE), fill=CHALK_DIM
        )
    for root in (2, 3):
        xpos = left + (right - left) * root / 6
        board.dot(xpos, axis_y, ACCENT, 9)
        board.draw.text(
            (xpos - 22, axis_y - 62),
            board.t(f"x={root}", f"x={root}"),
            font=font(34),
            fill=ACCENT,
        )

    stamp(board, 2)
    return board.finish()


def make_trig(cjk: bool) -> Image.Image:
    """③ 三角函数图像要点。"""
    board = Board(cjk)
    board.title(
        board.t("三角函数图像与性质", "Trigonometric Graphs and Properties"),
        board.t("高一 · 三角", "Grade 10"),
    )
    board.line(board.t("y = sin x：定义域 R，值域 [−1, 1]，周期 2π",
                       "y = sin x:  domain R, range [-1, 1], period 2pi"))
    board.line(board.t("y = cos x：值域 [−1, 1]，周期 2π，图像由 sin x 左移 π/2",
                       "y = cos x:  range [-1, 1], period 2pi, sin x shifted by pi/2"))
    board.line(board.t("五点法（一个周期）：x = 0, π/2, π, 3π/2, 2π → y = 0, 1, 0, −1, 0",
                       "Five key points: x = 0, pi/2, pi, 3pi/2, 2pi -> y = 0, 1, 0, -1, 0"))
    board.line(board.t("y = A sin(ωx + φ)：振幅 |A|，周期 2π/|ω|，初相 φ",
                       "y = A sin(wx + p):  amplitude |A|, period 2pi/|w|, phase p"))
    board.line(board.t("单调递增 [−π/2 + 2kπ, π/2 + 2kπ]，递减 [π/2 + 2kπ, 3π/2 + 2kπ]",
                       "Increasing on [-pi/2 + 2kpi, pi/2 + 2kpi], decreasing after"))
    board.line(board.t("对称轴 x = π/2 + kπ，对称中心 (kπ, 0)",
                       "Axis x = pi/2 + kpi,  center of symmetry (kpi, 0)"))
    board.blank(8)

    pi = math.pi
    box = (MARGIN + 20, board.cursor, CANVAS_SIZE[0] - MARGIN - 20, 1140)
    px, py = board.axes(box, (-0.35 * pi, 2.35 * pi), (-1.45, 1.45))

    ticks = (
        (0.5 * pi, "π/2", "pi/2"),
        (pi, "π", "pi"),
        (1.5 * pi, "3π/2", "3pi/2"),
        (2 * pi, "2π", "2pi"),
    )
    for tick, zh_label, en_label in ticks:
        board.draw.line([(px(tick), py(0)), (px(tick), py(1.25))], fill=(70, 96, 84), width=2)
        board.tag(px(tick), py(0), zh_label if board.cjk else en_label, CHALK_DIM, dx=-16, dy=14)
    for value in (-1.0, 1.0):
        board.tag(px(2.3 * pi), py(value), f"{value:g}", CHALK_DIM, dx=10, dy=-12)

    board.curve(px, py, math.sin, 0.0, 2 * pi, GRAPH_COLOR, 5)
    board.curve(px, py, math.cos, 0.0, 2 * pi, GRAPH_ALT, 3)
    board.tag(px(0.5 * pi), py(1.0), "y = sin x", GRAPH_COLOR, dx=-146, dy=-54)
    board.tag(px(pi), py(-1.0), "y = cos x", GRAPH_ALT, dx=24, dy=22)

    stamp(board, 3)
    return board.finish()


# ---------------------------------------------------------------- 入口

SAMPLES = (
    ("sample-quadratic.png", make_quadratic),
    ("sample-equation.png", make_root_formula),
    ("sample-trig.png", make_trig),
)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if HAS_CJK:
        print(f"中文字体：{FONT_PATH}")
    else:
        print("未找到中文字体（C:/Windows/Fonts/msyh.ttc 等），已退化为英文版样例图。")
        print("如需中文图，请安装微软雅黑或在 CJK_FONT_CANDIDATES 中补充字体路径。")

    failures = 0
    for name, builder in SAMPLES:
        target = OUTPUT_DIR / name
        try:
            image = builder(HAS_CJK)
            image.save(target, "PNG", optimize=True)
        except Exception as exc:  # 单张失败不拖垮其余
            failures += 1
            print(f"失败：{name} -> {type(exc).__name__}: {exc}")
            continue
        size_kb = target.stat().st_size / 1024
        print(f"已生成：{target.relative_to(PROJECT_ROOT)}  "
              f"{image.width}x{image.height}  {size_kb:.1f} KB")

    print(f"共 {len(SAMPLES) - failures}/{len(SAMPLES)} 张，输出目录 "
          f"{OUTPUT_DIR.relative_to(PROJECT_ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
