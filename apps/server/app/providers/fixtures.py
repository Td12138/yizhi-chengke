"""Mock 数据源：无密钥、无网络也能跑通完整漏斗，同时充当演示用的稳定数据。

所有内容均为自造的教学样例（二次函数），不含任何真实资料。

注意：mock 讲解（`mock_explain`）是**按题干关键词挑模板的固定样例文案**，不是模型生成的，
也不是通解——它只覆盖对称轴 / 顶点 / 交点（判别式）/ 配方 / 代入求值五类问法，
参数 a、b、c 从题干里解析，解析失败才退回样例题的系数。真实错因分析必须接 LLM。
"""

from __future__ import annotations

import re

import sympy as sp

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


# ---- mock 讲解：按题干分类的固定样例文案，不是模型输出 ----

# 样例题 y = x² - 4x + 3 的系数：题干解析不出来时退回这套值
SAMPLE_A, SAMPLE_B, SAMPLE_C = 1.0, -4.0, 3.0
# 讲解图窗口：以对称轴为中心左右各 VIEW_SPAN，保证顶点落在图里
VIEW_SPAN = 3.0
# 题干没写代入点时用这个 x（样例题是「在 x = 2 处」）
SAMPLE_X = 2.0
# a 的退化判定：|a| 小于它就无法配方 / 求对称轴
DEGENERATE_EPS = 1e-9
# 数字文案保留的小数位
TEXT_DECIMALS = 2

# LaTeX 命令（\dfrac 等）在题干解析时整体丢掉
LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+")
# 题干里 y = ... 的表达式主体，遇到中文或标点即停
POLY_TEXT = re.compile(r"y=([0-9x+\-*/.^()]+)")
# 「在 x = 2 处」里的代入值
SUBST_VALUE = re.compile(r"x=(-?\d+(?:\.\d+)?)")
# 隐式乘号 4x → 4*x，sympy 才认
IMPLICIT_PRODUCT = re.compile(r"(\d)x")

# 问法分类：先命中先返回；配方排在顶点前面（「顶点式」也算配方）
EXPLAIN_TOPICS = (
    ("complete_square", ("配方", "顶点式", "完全平方")),
    ("intersection", ("交点", "零点", "判别式", "Δ", "实根")),
    ("axis", ("对称轴",)),
    ("vertex", ("顶点", "最值")),
    ("value", ("函数值", "代入")),
)
# 需要求对称轴的三类：a 退化时退回兜底文案
AXIS_TOPICS = ("axis", "vertex", "complete_square")


def _plain(text: str) -> str:
    """题干压成可解析的纯文本：去 LaTeX 命令、$、花括号与空白。"""
    text = LATEX_COMMAND.sub("", str(text))
    for noise in ("$", "{", "}", " ", "\t", "\n"):
        text = text.replace(noise, "")
    return text


def coefficients(stem: str) -> tuple[float, float, float]:
    """从题干解析 y = ax² + bx + c 的 a、b、c；解析失败退回样例题系数。"""
    match = POLY_TEXT.search(_plain(stem))
    expr_text = IMPLICIT_PRODUCT.sub(r"\1*x", match.group(1) if match else "")
    try:
        x = sp.Symbol("x")
        values = [float(value) for value in sp.Poly(sp.sympify(expr_text), x).all_coeffs()]
    except Exception:  # 解析不了就用样例值，讲解图照样能画
        return SAMPLE_A, SAMPLE_B, SAMPLE_C
    if len(values) != 3:  # all_coeffs 高次在前，三项才说明是二次式
        return SAMPLE_A, SAMPLE_B, SAMPLE_C
    return values[0], values[1], values[2]


def axis_of(a: float, b: float) -> float | None:
    """对称轴 x = -b/(2a)；a 退化成一次函数时返回 None。"""
    if abs(a) <= DEGENERATE_EPS:
        return None
    return -b / (2 * a)


def _fmt(value: float) -> str:
    """数字转文案：整数不带 .0，小数最多两位且不留尾零。"""
    if abs(value - round(value)) <= DEGENERATE_EPS:
        return str(int(round(value)))
    return f"{value:.{TEXT_DECIMALS}f}".rstrip("0").rstrip(".")


def _vertex_form(a: float, h: float, k: float) -> str:
    """顶点式文案：a(x - h)² + k，省掉系数 1 与尾巴 0。"""
    inner = f"x - {_fmt(h)}" if h >= 0 else f"x + {_fmt(-h)}"
    head = "" if abs(a - 1) <= DEGENERATE_EPS else _fmt(a)
    if abs(k) <= DEGENERATE_EPS:
        tail = ""
    elif k > 0:
        tail = f" + {_fmt(k)}"
    else:
        tail = f" - {_fmt(-k)}"
    return f"{head}({inner})²{tail}"


def _params(a: float, b: float, c: float) -> dict:
    """讲解图参数：a/b/c 用题干系数，窗口跟着对称轴走。"""
    center = axis_of(a, b)
    center = 0.0 if center is None else center
    return {
        "a": a,
        "b": b,
        "c": c,
        "x_min": center - VIEW_SPAN,
        "x_max": center + VIEW_SPAN,
    }


def _topic(stem: str) -> str:
    for topic, keywords in EXPLAIN_TOPICS:
        if any(keyword in stem for keyword in keywords):
            return topic
    return "fallback"


def _subst_x(stem: str) -> float:
    match = SUBST_VALUE.search(_plain(stem))
    return float(match.group(1)) if match else SAMPLE_X


def mock_explain(question: dict, user_answer: str = "") -> dict:
    """按题干挑一类讲解样例，params 取题干里的系数（拿不到才用样例题）。"""
    stem = str(question.get("stem_latex") or question.get("stem") or "")
    a, b, c = coefficients(stem)
    answer = str(user_answer).strip()
    lead = f"你填的是「{answer}」，" if answer else ""
    topic = _topic(stem)
    axis = axis_of(a, b)
    if topic in AXIS_TOPICS and axis is None:
        topic = "fallback"

    if topic == "axis":
        title = "对称轴：-b/2a 就是那条竖线"
        script = (
            f"{lead}对称轴只由 a、b 决定：x = -b/(2a) = "
            f"-({_fmt(b)})/(2×{_fmt(a)}) = {_fmt(axis)}。"
            "题目问的是对称轴位置，填这个数或「x = 这个数」都算对。"
        )
    elif topic == "vertex":
        vertex_y = a * axis * axis + b * axis + c
        title = "顶点落在对称轴上"
        script = (
            f"{lead}顶点横坐标就是对称轴 x = -b/(2a) = {_fmt(axis)}，"
            f"代回原式得 y = {_fmt(vertex_y)}，所以顶点是 ({_fmt(axis)}, {_fmt(vertex_y)})。"
        )
    elif topic == "intersection":
        delta = b * b - 4 * a * c
        if delta > DEGENERATE_EPS:
            conclusion = "Δ > 0，抛物线与 x 轴有两个交点"
        elif delta < -DEGENERATE_EPS:
            conclusion = "Δ < 0，抛物线与 x 轴没有交点"
        else:
            conclusion = "Δ = 0，抛物线与 x 轴只有一个交点（重根）"
        title = "Δ 的符号决定交点个数"
        script = (
            f"{lead}判别式 Δ = b² - 4ac = ({_fmt(b)})² - 4×{_fmt(a)}×{_fmt(c)}"
            f" = {_fmt(delta)}，{conclusion}。判断交点个数不用画图，只看 Δ。"
        )
    elif topic == "complete_square":
        vertex_y = a * axis * axis + b * axis + c
        title = "配方：补一半再减掉"
        script = (
            f"{lead}配方是把 ax² + bx 补成完全平方：本题 a={_fmt(a)}、b={_fmt(b)}、"
            f"c={_fmt(c)}，配完得 y = {_vertex_form(a, axis, vertex_y)}，"
            "展开回去还能得到原式就说明没配错。"
        )
    elif topic == "value":
        x0 = _subst_x(stem)
        value = a * x0 * x0 + b * x0 + c
        title = "代入求值按顺序算"
        script = (
            f"{lead}把 x = {_fmt(x0)} 代进去分三步：x² = {_fmt(x0 * x0)}，"
            f"{_fmt(b)}x = {_fmt(b * x0)}，再加常数 {_fmt(c)}，得 {_fmt(value)}。"
            "符号算反是这类题最常见的错。"
        )
    else:
        title = "先判断题目问的是哪个量"
        if axis is None:
            script = (
                f"{lead}题干里没识别出二次项系数 a，先确认它是不是二次函数；"
                "把式子整理成 y = ax² + bx + c 再套公式。"
            )
        else:
            script = (
                f"{lead}先看清题目要对称轴、顶点、交点个数还是顶点式；"
                f"本题 a={_fmt(a)}、b={_fmt(b)}、c={_fmt(c)}，"
                f"对称轴 x = -b/(2a) = {_fmt(axis)}，从这里往下推。"
            )

    return {
        "template": "function_slider",
        "title": title,
        "params": _params(a, b, c),
        "script": script,
    }

