"""Mock 数据源：无密钥、无网络也能跑通完整漏斗，同时充当演示用的稳定数据。

所有内容均为自造的教学样例（二次函数），不含任何真实资料。
"""

from __future__ import annotations

# 识别 Agent 的模拟输出：模拟一张板书的结构化结果
MOCK_BLOCKS = [
    {"type": "title", "content": "二次函数的图象与性质"},
    {
        "type": "formula",
        "content": "y = ax^2 + bx + c",
        "latex": r"y = ax^2 + bx + c \quad (a \neq 0)",
    },
    {
        "type": "formula",
        "content": "y = a(x + b/(2a))^2 + (4ac - b^2)/(4a)",
        "latex": r"y = a\left(x + \dfrac{b}{2a}\right)^{2} + \dfrac{4ac - b^{2}}{4a}",
    },
    {
        "type": "text",
        "content": "顶点坐标 (-b/(2a), (4ac-b^2)/(4a))，对称轴 x = -b/(2a)",
    },
    {"type": "text", "content": "当 a>0 时开口向上，a<0 时开口向下"},
    {
        "type": "formula",
        "content": "判别式 D = b^2 - 4ac",
        "latex": r"\Delta = b^{2} - 4ac",
    },
    {"type": "text", "content": "例：y = x^2 - 4x + 3，求顶点与零点"},
    {
        "type": "table",
        "content": "a>0 开口向上 / a<0 开口向下；Δ>0 两零点，Δ=0 一个零点，Δ<0 无实零点",
    },
]

MOCK_CARDS = [
    {
        "title": "二次函数的一般式",
        "summary": "y = ax² + bx + c（a≠0），a 决定开口方向",
        "subject": "数学",
        "tags": ["二次函数", "一般式"],
        "formula": r"y = ax^{2} + bx + c",
        "body": "二次函数的一般式。a>0 开口向上，a<0 开口向下；|a| 越大开口越窄。",
    },
    {
        "title": "顶点式与对称轴",
        "summary": "配方后可读出顶点与对称轴位置",
        "subject": "数学",
        "tags": ["二次函数", "顶点式", "配方"],
        "formula": r"y = a\left(x + \dfrac{b}{2a}\right)^{2} + \dfrac{4ac - b^{2}}{4a}",
        "body": (
            "由一般式配方得顶点式：顶点坐标为 (-b/2a, (4ac-b²)/4a)，"
            "对称轴为 x = -b/2a。配方是把一般式化为顶点式的关键步骤。"
        ),
    },
    {
        "title": "判别式与零点个数",
        "summary": "Δ = b² - 4ac 决定抛物线与 x 轴的交点个数",
        "subject": "数学",
        "tags": ["二次函数", "判别式", "零点"],
        "formula": r"\Delta = b^{2} - 4ac",
        "body": "Δ>0 有两个不同实零点；Δ=0 有一个重零点；Δ<0 无实零点。",
    },
]


def mock_questions(round_no: int) -> list[dict]:
    """round_no=1 故意留一道「答案不自洽」的题，供质检 Agent 当场驳回。"""
    wrong_expect = 1.0 if round_no == 1 else -1.0
    return [
        {
            "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的对称轴。",
            "qtype": "fill",
            "options": [],
            "answer": r"$x = 2$",
            "solution": r"对称轴 $x = -\dfrac{b}{2a} = -\dfrac{-4}{2} = 2$。",
            "difficulty": "easy",
            "verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": -1.0},
        },
        {
            "stem_latex": r"求 $y = x^{2} - 4x + 3$ 的顶点坐标。",
            "qtype": "fill",
            "options": [],
            "answer": r"$(2,\ -1)$",
            "solution": r"顶点横坐标 $x=2$，代入得 $y = 4-8+3 = -1$。",
            "difficulty": "medium",
            "verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": -1.0},
        },
        {
            "stem_latex": r"$y = x^{2} - 4x + 3$ 与 $x$ 轴的交点个数是？",
            "qtype": "choice",
            "options": ["0 个", "1 个", "2 个", "无法确定"],
            "answer": "C",
            "solution": r"$\Delta = 16 - 12 = 4 > 0$，故有两个交点。",
            "difficulty": "easy",
            "verify": {"expr": "(-4)**2 - 4*1*3", "subs": {}, "expect": 4.0},
        },
        {
            "stem_latex": r"若 $y = x^{2} - 4x + 3$ 在 $x = 2$ 处的函数值为 $m$，则 $m$ 等于？",
            "qtype": "fill",
            "options": [],
            "answer": r"$-1$",
            "solution": r"代入 $x=2$：$4 - 8 + 3 = -1$。",
            "difficulty": "medium",
            "verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": wrong_expect},
        },
        {
            "stem_latex": r"把 $y = x^{2} - 4x + 3$ 化为顶点式。",
            "qtype": "fill",
            "options": [],
            "answer": r"$y = (x-2)^{2} - 1$",
            "solution": r"$x^{2}-4x+3 = (x-2)^{2}-1$。",
            "difficulty": "hard",
            "verify": {
                "expr": "(x - 2)**2 - 1 - (x**2 - 4*x + 3)",
                "subs": {"x": 5},
                "expect": 0.0,
            },
        },
    ]


MOCK_EXPLAIN = {
    "template": "function_slider",
    "title": "拖动 a 看开口怎么变",
    "params": {"a": 1.0, "b": -4.0, "c": 3.0, "x_min": -1.0, "x_max": 5.0},
    "script": (
        "你把函数值算成了正数，问题出在代入顺序：先把 x=2 代进 x² 得 4，"
        "再减 4x 得 -4，最后加 3，结果是 -1。拖动 a 的滑块可以看到，"
        "a 只改变开口方向和宽窄，不改变 x=2 处的取值规律。"
    ),
}
