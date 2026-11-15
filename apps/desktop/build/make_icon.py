"""生成桌面版图标 build/icon.ico（深色底 + 青色纸页折角）。

用宿主机 Python 运行：python build/make_icon.py
仅依赖 Pillow；输出路径由脚本自身位置推导，不含硬编码盘符。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT = SCRIPT_DIR / "icon.ico"

CANVAS = 1024  # 先大图绘制再降采样，边缘更干净
BG_COLOR = (14, 19, 32, 255)  # #0e1320
ACCENT = (45, 212, 191, 255)  # #2dd4bf
PAPER = (248, 250, 252, 255)
PAPER_SHADOW = (15, 23, 42, 255)
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def build_master() -> Image.Image:
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 深色圆角底板
    rounded_rect(draw, (32, 32, CANVAS - 32, CANVAS - 32), 200, BG_COLOR)

    # 纸页阴影 + 纸页主体（右上角折角）
    left, top, right, bottom = 250, 190, 774, 834
    fold = 150
    shadow_offset = 18
    shadow = [
        (left + shadow_offset, top + shadow_offset),
        (right - fold + shadow_offset, top + shadow_offset),
        (right + shadow_offset, top + fold + shadow_offset),
        (right + shadow_offset, bottom + shadow_offset),
        (left + shadow_offset, bottom + shadow_offset),
    ]
    draw.polygon(shadow, fill=PAPER_SHADOW)

    paper = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    draw.polygon(paper, fill=PAPER)

    # 折角：青色三角形
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=ACCENT,
    )

    # 纸面上的三条内容线（最后一条短）
    line_x0, line_x1 = left + 78, right - 86
    line_ys = [top + 250, top + 350, top + 450]
    for index, y in enumerate(line_ys):
        x1 = line_x1 if index < len(line_ys) - 1 else line_x1 - 120
        draw.rounded_rectangle((line_x0, y, x1, y + 34), radius=17, fill=(15, 23, 42, 200))

    # 右下角青色强调块：代表「生成的一课」
    draw.rounded_rectangle((right - 250, bottom - 130, right - 70, bottom - 50), radius=24, fill=ACCENT)

    return img


def main() -> int:
    master = build_master()
    frames = [master.resize(size, Image.LANCZOS) for size in SIZES]
    frames[0].save(OUTPUT, format="ICO", sizes=SIZES, append_images=frames[1:])
    print(f"icon written: {OUTPUT} ({OUTPUT.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
