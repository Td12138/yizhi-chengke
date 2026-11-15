"""Mock 数据源：无密钥、无网络也能跑通完整漏斗，同时充当演示用的稳定数据。

所有内容均为自造的教学样例（二次函数 / 一元二次方程 / 三角函数三套），不含任何真实资料。
三套样例按文件名关键字（quadratic / equation / trig，「二次函数 / 方程 / 三角」也行）分流，
认不出文件名时回落到二次函数那套，保证「换一张图，知识点跟着变」。

注意：mock 讲解（`mock_explain`）是**按题干关键词挑模板的固定样例文案**，不是模型生成的，
也不是通解——它只覆盖对称轴 / 顶点 / 交点（判别式）/ 配方 / 代入求值五类问法，
参数 a、b、c 从题干里解析，解析失败才退回样例题的系数。真实错因分析必须接 LLM。

同样地，mock 批改（`mock_review`）**只能陈述它实际比对到了什么**：关键式是否出现、
数值是否相等、步骤是否被覆盖。它无法判断学生「用了什么方法」，所以：

* `method_comparison` 只描述推荐解法（几步、为什么推荐），不出现学生步骤数；
* `steps[].note` 只引用作答里真实出现的片段，不推测「你省略了第 N 步」；
* `points[].issue` 用「未体现 / 与…不一致」这种可核对的措辞。

方法论层面的推断（「你用了求导，其实配方更快」）属于真实模型模式，见 `agents/review.py`。
"""

from __future__ import annotations

import re
from pathlib import Path

import sympy as sp

from ..judge import (
    FULLWIDTH_TABLE,
    NUMBER_TOLERANCE,
    answer_forms,
    judge,
    normalize,
)

# ---- 样例集：按文件名关键字分流，换一张图知识点跟着变 ----
# 文件名关键字 → 样例集，按顺序命中第一个。同时覆盖：
#   仓库样例图 01-二次函数… / 02-一元二次方程… / 03-三角…
#   演示用英文名 sample-quadratic / sample-equation / sample-trig
#   看不出主题但属于试题的 sample-exam.pdf（归到一元二次方程）
SAMPLE_KEYWORDS = (
    ("trig", ("trig", "三角", "正弦", "余弦")),
    ("equation", ("equation", "方程", "求根", "exam")),
    ("quadratic", ("quadratic", "二次函数", "抛物线")),
)

SET_LABELS = {
    "quadratic": "二次函数",
    "equation": "一元二次方程",
    "trig": "三角函数",
}

# 默认集：文件名认不出时用它（老演示数据与现有测试都依赖这套内容）
DEFAULT_SET = "quadratic"

# 识别 Agent 的模拟输出：模拟一张板书的结构化结果
QUADRATIC_BLOCKS = [
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

QUADRATIC_CARDS = [
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

EQUATION_BLOCKS = [
    {"type": "title", "content": "一元二次方程与求根公式"},
    {
        "type": "formula",
        "content": "ax^2 + bx + c = 0 (a != 0)",
        "latex": r"ax^{2} + bx + c = 0 \quad (a \neq 0)",
    },
    {
        "type": "formula",
        "content": "x = (-b ± sqrt(b^2 - 4ac)) / (2a)",
        "latex": r"x = \dfrac{-b \pm \sqrt{b^{2} - 4ac}}{2a}",
    },
    {
        "type": "formula",
        "content": "D = b^2 - 4ac",
        "latex": r"\Delta = b^{2} - 4ac",
    },
    {"type": "text", "content": "Δ>0 两个不等实根；Δ=0 两个相等实根；Δ<0 无实根"},
    {
        "type": "text",
        "content": "因式分解：若 x1、x2 是两根，则 ax²+bx+c = a(x-x1)(x-x2)",
    },
    {"type": "text", "content": "韦达定理：x1+x2 = -b/a，x1x2 = c/a"},
    {
        "type": "table",
        "content": "Δ>0 两个实根 / Δ=0 一个重根 / Δ<0 无实根；因式分解适合有有理根的情形",
    },
]

EQUATION_CARDS = [
    {
        "title": "求根公式",
        "summary": "ax²+bx+c=0 的两根可由系数直接算出",
        "subject": "数学",
        "tags": ["一元二次方程", "求根公式"],
        "formula": r"x = \dfrac{-b \pm \sqrt{b^{2} - 4ac}}{2a}",
        "body": (
            "先把方程化成 ax²+bx+c=0，再算 Δ=b²-4ac 代入求根公式。"
            "a 出现在分母，所以必须 a≠0。"
        ),
    },
    {
        "title": "根的判别式",
        "summary": "Δ = b² - 4ac 决定实根的个数",
        "subject": "数学",
        "tags": ["一元二次方程", "判别式", "根的个数"],
        "formula": r"\Delta = b^{2} - 4ac",
        "body": (
            "Δ>0 两个不等实根；Δ=0 两个相等实根（重根）；"
            "Δ<0 无实根，但在复数范围内有一对共轭复根。"
        ),
    },
    {
        "title": "因式分解法",
        "summary": "把方程拆成两个一次因式的乘积再取根",
        "subject": "数学",
        "tags": ["一元二次方程", "因式分解"],
        "formula": r"ax^{2} + bx + c = a(x - x_1)(x - x_2)",
        "body": (
            "十字相乘或先求根再拆因式；能分解时比套公式快，"
            "但只适用于有有理根的情形。"
        ),
    },
    {
        "title": "根与系数的关系",
        "summary": "不解方程也能求两根之和与两根之积",
        "subject": "数学",
        "tags": ["一元二次方程", "韦达定理"],
        "formula": r"x_1 + x_2 = -\dfrac{b}{a},\quad x_1 x_2 = \dfrac{c}{a}",
        "body": "韦达定理常用于求参数、构造以两根为根的新方程，或检验求出的根是否合理。",
    },
]

TRIG_BLOCKS = [
    {"type": "title", "content": "三角函数的图象与性质"},
    {
        "type": "formula",
        "content": "sin(pi + a) = -sin a, cos(pi + a) = -cos a",
        "latex": r"\sin(\pi + \alpha) = -\sin\alpha,\quad \cos(\pi + \alpha) = -\cos\alpha",
    },
    {
        "type": "formula",
        "content": "sin(a + b) = sin a cos b + cos a sin b",
        "latex": r"\sin(\alpha + \beta) = \sin\alpha\cos\beta + \cos\alpha\sin\beta",
    },
    {"type": "text", "content": "y=sin x 与 y=cos x 的最小正周期 T=2π，y=tan x 的周期 T=π"},
    {"type": "text", "content": "y = A sin(ωx + φ) 的周期 T = 2π/|ω|，振幅为 |A|"},
    {
        "type": "table",
        "content": "第一象限全正 / 第二象限 sin 正 / 第三象限 tan 正 / 第四象限 cos 正",
    },
]

TRIG_CARDS = [
    {
        "title": "诱导公式",
        "summary": "把任意角的三角函数化到锐角",
        "subject": "数学",
        "tags": ["三角函数", "诱导公式"],
        "formula": r"\sin(\pi + \alpha) = -\sin\alpha",
        "body": (
            "口诀「奇变偶不变，符号看象限」：先看 π/2 的奇数倍还是偶数倍，"
            "再把 α 当锐角判断原函数在新象限的符号。"
        ),
    },
    {
        "title": "三角函数的图像与周期",
        "summary": "正弦、余弦、正切的周期与振幅各不相同",
        "subject": "数学",
        "tags": ["三角函数", "周期", "图像"],
        "formula": r"T = \dfrac{2\pi}{|\omega|}",
        "body": (
            "y=sin x、y=cos x 的周期是 2π，y=tan x 是 π；"
            "y=A sin(ωx+φ) 的周期只由 ω 决定，振幅由 |A| 决定。"
        ),
    },
    {
        "title": "两角和与差公式",
        "summary": "把复合角拆成两个单角的三角函数",
        "subject": "数学",
        "tags": ["三角函数", "和角公式"],
        "formula": r"\sin(\alpha \pm \beta) = \sin\alpha\cos\beta \pm \cos\alpha\sin\beta",
        "body": (
            "sin 展开是「正余余正，符号同」；cos 展开是「余余正正，符号反」。"
            "和角公式是推导二倍角公式的基础。"
        ),
    },
]

SETS = {
    "quadratic": {"blocks": QUADRATIC_BLOCKS, "cards": QUADRATIC_CARDS},
    "equation": {"blocks": EQUATION_BLOCKS, "cards": EQUATION_CARDS},
    "trig": {"blocks": TRIG_BLOCKS, "cards": TRIG_CARDS},
}

# 兼容旧引用：默认集就是以前的「那一套」
MOCK_BLOCKS = QUADRATIC_BLOCKS
MOCK_CARDS = QUADRATIC_CARDS


def detect_set(source: str | Path | None) -> str | None:
    """按文件名关键字选样例集；认不出返回 None（由调用方回落到默认）。"""
    if not source:
        return None
    name = Path(str(source)).name.lower()
    for key, keywords in SAMPLE_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return key
    return None


def match_note(source: str | Path | None) -> str:
    """事件日志用的一句话：命中哪套样例集，或说明回落到默认。"""
    key = detect_set(source)
    if key is None:
        return "未匹配到样例集，使用默认"
    return f"命中样例集：{SET_LABELS[key]}"


def blocks_for(source: str | Path | None) -> list[dict]:
    """按文件名取样例集的版面块；返回副本，调用方随便改不影响样例。"""
    key = detect_set(source) or DEFAULT_SET
    template = MOCK_BLOCKS if key == DEFAULT_SET else SETS[key]["blocks"]
    return [{**block} for block in template]


def cards_for(source: str | Path | None) -> list[dict]:
    """按文件名取样例集的卡片；tags 也复制，避免调用方改到样例本体。"""
    key = detect_set(source) or DEFAULT_SET
    template = MOCK_CARDS if key == DEFAULT_SET else SETS[key]["cards"]
    return [{**card, "tags": list(card.get("tags", []))} for card in template]



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


# ---- mock 批改：确定性规则打分（同样的作答永远得同样的分，无随机数）----
#
# 分数档位不是「拍脑袋的宽松度」，而是判分口径：
#   与标准答案完全一致 → 100 / correct
#   数值等价（judge 规则）或命中关键式 → 85 / correct
#   部分要点命中 → 40 起步、每个要点 +15，封顶 70 / partial
#   完全无关 → 10 起步、每出现一个相关符号 +5，封顶 30 / wrong

EXACT_SCORE = 100
EQUIVALENT_SCORE = 85
PARTIAL_BASE = 40
PARTIAL_STEP = 15
PARTIAL_MAX = 70
WRONG_BASE = 10
WRONG_STEP = 5
WRONG_MAX = 30
# 关键式至少这么长才做「包含」判断：1-2 字符的关键式（如选项字母）太容易巧合命中
KEY_FORM_MIN_CHARS = 3

VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_WRONG = "wrong"

# 判分时只会归一化成小写（normalize），关键词表也统一按小写比对

# 「完全无关」档也要区分「动过笔」与「整段空白」：出现这些符号说明至少算了点什么
RELEVANT_MARKS = ("x", "y", "=", "平方", "交点", "对称轴", "δ", "配方")

# 每类问法的得分点：keywords 命中其一，或 need_value 时与标准答案数值等价
# issue 是「该点没命中时的扣分原因」，按题目类型给不同文案
TOPIC_POINTS: dict[str, tuple[dict, ...]] = {
    "axis": (
        {
            "text": "写出对称轴公式 x = -b/(2a)",
            "keywords": ("-b/2a", "-b/(2a)", "对称轴"),
            "issue": "作答里没有出现对称轴公式 x = -b/(2a)",
        },
        {
            "text": "把本题的 a、b 代进公式",
            "keywords": ("a=1", "b=-4", "-4"),
            "issue": "作答里没有出现 a = 1、b = -4 的代入",
        },
        {
            "text": "算出横坐标 x = 2",
            "need_value": True,
            "issue": "横坐标与标准答案 x = 2 不一致（只写 2 也算对）",
        },
    ),
    "vertex": (
        {
            "text": "先用对称轴定横坐标",
            "keywords": ("对称轴", "-b/2a", "-b/(2a)"),
            "issue": "作答里没有出现对称轴，横坐标的依据未体现",
        },
        {
            "text": "把横坐标代回原式求纵坐标",
            "keywords": ("代入", "代回", "函数值"),
            "issue": "未体现「代回原式求纵坐标」，纵坐标的计算过程缺失",
        },
        {
            "text": "写成坐标 (2, -1)",
            "need_value": True,
            "issue": "顶点坐标与 (2, -1) 不一致（缺括号或数值对不上）",
        },
    ),
    "intersection": (
        {
            "text": "用判别式 Δ = b² - 4ac 判断",
            "keywords": ("δ", "δ=b²-4ac", "判别式", "b²-4ac"),
            "issue": "作答里没有出现判别式 Δ = b² - 4ac",
        },
        {
            "text": "算出 Δ = 4 > 0",
            "keywords": ("δ=4", "=4", "16-12"),
            "issue": "判别式的值与 Δ = 4 不一致（16 - 12）",
        },
        {
            "text": "结论落到「两个交点」",
            "keywords": ("两个交点", "2个交点", "两个不同", "两个零点"),
            "need_value": True,
            "issue": "结论未落到「两个交点」：Δ > 0 有两个不同实零点",
        },
    ),
    "complete_square": (
        {
            "text": "用配方化成完全平方",
            "keywords": ("配方", "完全平方"),
            "issue": "未体现配方过程（补一半的平方再减掉）",
        },
        {
            "text": "做一次展开还原校验",
            "keywords": ("展开", "还原", "检验", "等价"),
            "issue": "未体现「展开还原」校验",
        },
        {
            "text": "结果写成 y = (x-2)² - 1",
            "need_value": True,
            "issue": "顶点式与标准答案 y = (x-2)² - 1 不一致",
        },
    ),
    "value": (
        {
            "text": "把 x = 2 代入原式",
            "keywords": ("x=2", "代入", "代进"),
            "issue": "作答里没有出现代入点 x = 2",
        },
        {
            "text": "按 x²、bx、常数 的顺序算",
            "keywords": ("x²", "^2", "平方"),
            "issue": "未体现按 x²、bx、常数 顺序计算",
        },
        {
            "text": "得到 m = -1",
            "need_value": True,
            "issue": "结果与 -1 不一致（4 - 8 + 3）",
        },
    ),
    "fallback": (
        {
            "text": "先判断题目问的是哪个量",
            "keywords": ("对称轴", "顶点", "交点", "配方", "代入", "函数值"),
            "issue": "未说明题目问的是哪个量",
        },
        {
            "text": "写出用到的那条公式",
            "keywords": ("-b/2a", "-b/(2a)", "δ", "判别式", "配方"),
            "issue": "没写出所用公式，只有结论无法判分",
        },
        {
            "text": "给出最终结果",
            "need_value": True,
            "issue": "最终结果与标准答案不一致",
        },
    ),
}

# 每类问法的改进建议：满分与未满分给不同文案，避免一套模板套到底
TOPIC_ADVICE: dict[str, dict[str, str]] = {
    "axis": {
        "full": "公式和结果都对。再口述一遍「为什么对称轴是 -b/(2a)」，这题就彻底稳了。",
        "worse": "对称轴只由 a、b 决定：先写 x = -b/(2a)，再代数字，最后写成 x = 数值。",
    },
    "vertex": {
        "full": "两步都到位：横坐标来自对称轴，纵坐标代回原式。可以试试先配方再读顶点。",
        "worse": "顶点横坐标来自对称轴，纵坐标必须代回原式；两步分开写，别只写一个数。",
    },
    "intersection": {
        "full": "判别式用得正确。记住结论链：Δ>0 两个交点、Δ=0 一个、Δ<0 没有。",
        "worse": "判断交点个数不用画图：先算 Δ = b² - 4ac，再按 Δ 的符号下结论。",
    },
    "complete_square": {
        "full": "配方结果与标准答案一致。再展开验算一遍，能确认等价就更稳。",
        "worse": "配方口诀：提出 a、补一半的平方、再减掉同一项；配完展开一遍验证等价。",
    },
    "value": {
        "full": "代入顺序和符号都对。遇到负系数时先把负号拎出来再算，速度更快。",
        "worse": "代入求值按 x² → bx → 常数 的顺序算，每步单独写，负号就不容易丢。",
    },
    "fallback": {
        "full": "结果正确。可以再说明用了哪条公式，过程分也能拿满。",
        "worse": "先判断题目问的是哪个量，再挑对应公式；答案写成「x = 数值」或坐标形式。",
    },
}

# 完全无关时给一张「该回去看哪张卡片」的提示，按题目类型指向不同卡片
TOPIC_CARD: dict[str, str] = {
    "axis": "顶点式与对称轴",
    "vertex": "顶点式与对称轴",
    "complete_square": "顶点式与对称轴",
    "intersection": "判别式与零点个数",
    "value": "二次函数的一般式",
    "fallback": "二次函数的一般式",
}

# ---- 步骤诊断：solution 切步 → 逐步比对 → 定位第一个错步 ----

# solution 按句号/分号/换行切成步骤
STEP_SPLIT = re.compile(r"[；;。\n]+")
# 切出来只有 1 段时，用题型模板补齐，保证 2–5 步
MIN_STEPS, MAX_STEPS = 2, 5
STEP_STRIP = " ，,"
# 步骤里的数值：排除 2a 这种系数写法（数字后面紧跟字母不算结果值）
STEP_NUMBER = re.compile(r"(?<![a-z0-9.])[+-]?\d+(?:\.\d+)?(?![a-z0-9.])")
# 结果值判定：末尾数字前面若是数字/字母/小数点/正负号，说明它只是公式的一部分
STEP_NUMBER_TAIL = re.compile(r"[+-]?\d+(?:\.\d+)?")
FORMULA_PREFIX = set("0123456789.+-abcdefghijklmnopqrstuvwxyz")
# 中文连续片段：步骤与作答共享这么多 2-gram 才算「提到了这一步」
CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
GRAM_HITS = 2
# 纯结果式作答：只有数值（可带 x= / y= 与逗号分隔的坐标），没有任何推导文字
RESULT_ONLY = re.compile(
    r"^(?:[a-z]=)?[+-]?\d+(?:\.\d+)?(?:,(?:[a-z]=)?[+-]?\d+(?:\.\d+)?)*$"
)
# note 里引用学生原文的最大长度
NOTE_QUOTE_CHARS = 24

# solution 切不出 2 段时按题型补的步骤模板（{answer} 用题目标准答案填，保证可复现）
TOPIC_STEPS: dict[str, tuple[str, ...]] = {
    "axis": (
        "写出对称轴公式 x = -b/(2a)",
        "把 a、b 代入算出 {answer}",
    ),
    "vertex": (
        "先用对称轴公式求横坐标",
        "把横坐标代回原式求纵坐标",
        "写成顶点坐标 {answer}",
    ),
    "intersection": (
        "算判别式 Δ = b² - 4ac",
        "按 Δ 的符号判交点个数：{answer}",
    ),
    "complete_square": (
        "把一般式配方成完全平方",
        "写成顶点式 {answer}",
    ),
    "value": (
        "把 x 的取值代入原式",
        "算出结果 {answer}",
    ),
    "fallback": (
        "判断题目问的是哪个量",
        "套对应公式并代入",
        "写出最终结果 {answer}",
    ),
}

# 更优解法：按题型固定映射（学生常见解法 → 推荐解法），不随机挑选
# 更优解法：按题型固定映射。
#
# 只描述「推荐什么、为什么推荐」，**不描述学生的方法**——mock 只做字符串/数值比对，
# 没有能力判断学生用了求导还是配方，写「你的解法 N 步」就是编造。
# 方法论推断只在真实模型模式下由模型给出（见 agents/review.py 的 coerce_review）。
BETTER_METHODS: dict[str, dict] = {
    "axis": {
        "name": "顶点公式",
        "why": "一次代入，不用求导",
        "steps": ("读出一般式的 a、b", "代 x = -b/(2a) 直接得对称轴"),
        "better_steps": 2,
        "signature": ("-b/2a", "-b/(2a)", "顶点公式"),
    },
    "vertex": {
        "name": "顶点公式",
        "why": "横纵坐标一次算出，不必求导",
        "steps": ("代 x = -b/(2a) 得横坐标", "代回原式得纵坐标，写成坐标"),
        "better_steps": 2,
        "signature": ("-b/2a", "-b/(2a)", "顶点公式"),
    },
    "intersection": {
        "name": "判别式",
        "why": "不算根也能判个数",
        "steps": ("算 Δ = b² - 4ac", "按 Δ 的符号直接下结论"),
        "better_steps": 2,
        "signature": ("δ", "判别式"),
    },
    "complete_square": {
        "name": "配方法",
        "why": "比展开后通分少一步，出错面更小",
        "steps": ("提出二次项系数，补一半的平方", "写成 a(x-h)²+k 并展开校验"),
        "better_steps": 3,
        "signature": ("配方", "完全平方"),
    },
    "value": {
        "name": "直接代入",
        "why": "本题无化简收益",
        "steps": ("把 x 的值直接代进原式", "按 x²、bx、c 的顺序算出结果"),
        "better_steps": 2,
        "signature": ("代入", "代进"),
    },
    "fallback": {
        "name": "先定性再套公式",
        "why": "先判断题眼，少走回头路",
        "steps": ("判断题目问的是对称轴/顶点/交点/配方/代入", "选对应公式代入"),
        "better_steps": 2,
        "signature": (),
    },
}


def _grams(text: str) -> set[str]:
    """中文 2-gram 集合：中文没有空格，靠相邻两字判断是否在说同一件事。"""
    grams: set[str] = set()
    for run in CHINESE_RUN.findall(text):
        grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams


def _numbers(text: str) -> list[float]:
    return [float(token) for token in STEP_NUMBER.findall(text)]


def _step_target(step_text: str) -> float | None:
    """步骤末尾写出的结果值：`... x = 2` → 2；`... y=(x-2)²-1` → None（公式的一部分）。

    在**未去括号**的原文上取数：归一化会把 `(x-2)^2-1` 压成 `x-22-1`，符号就丢了。
    """
    tail = str(step_text).translate(FULLWIDTH_TABLE)
    tail = re.sub(r"[\s${}\\]", "", tail).rstrip(".。")
    matches = list(STEP_NUMBER_TAIL.finditer(tail))
    if not matches:
        return None
    last = matches[-1]
    if last.end() != len(tail):
        return None
    before = tail[last.start() - 1] if last.start() else ""
    if before and before.lower() in FORMULA_PREFIX:
        return None
    return float(last.group())


def _expectations(question: dict, topic: str) -> list[str]:
    """steps[].expect：优先按 solution 切句，切不出 2 步时用题型模板补。"""
    solution = str(question.get("solution") or "")
    parts = [part.strip(STEP_STRIP) for part in STEP_SPLIT.split(solution)]
    parts = [part for part in parts if part]
    if len(parts) >= MIN_STEPS:
        return parts[:MAX_STEPS]
    answer = str(question.get("answer") or "")
    return [template.format(answer=answer) for template in TOPIC_STEPS[topic]]


def _step_found(expect: str, plain_answer: str, answer_numbers: list[float]) -> bool:
    """这一步是否被作答覆盖：关键式命中 / 结果值命中 / 中文要点命中。

    步骤末尾写了结果值（`... x = 2`）时以数值为准——写了别的数就是这一步错了，
    不能因为出现了「代入」「算出」这些词就算过。
    """
    if not plain_answer:
        return False
    plain_step = normalize(expect)
    if len(plain_step) >= KEY_FORM_MIN_CHARS and plain_step in plain_answer:
        return True
    target = _step_target(expect)
    if target is not None:
        return any(abs(target - value) <= NUMBER_TOLERANCE for value in answer_numbers)
    return len(_grams(plain_step) & _grams(plain_answer)) >= GRAM_HITS


def _fmt_numbers(numbers: list[float]) -> str:
    return "、".join(_fmt(number) for number in numbers)


def _step_note(expect: str, found: bool, plain_answer: str, user_answer: str) -> str:
    """这一步的比对结论：只陈述「比对到了什么」，不推断学生用了什么方法。"""
    if found:
        return "已覆盖：作答里出现了这一步的关键式或结果值"
    if not plain_answer:
        return "缺失：没有作答"

    numbers = _numbers(plain_answer)
    target = _step_target(expect)
    if target is not None and numbers:
        return f"缺失：这一步该得到 {_fmt(target)}，作答里的数值是 {_fmt_numbers(numbers)}"

    quoted = str(user_answer).strip().replace("\n", " ")
    if len(quoted) > NOTE_QUOTE_CHARS:
        quoted = quoted[:NOTE_QUOTE_CHARS] + "…"
    if RESULT_ONLY.fullmatch(plain_answer.replace(" ", "")):
        return f"缺失：作答只有一个结果值 {_fmt_numbers(numbers)}，未体现推导过程"
    if target is not None:
        return f"缺失：这一步该得到 {_fmt(target)}，作答里没有对应数值"
    return f"缺失：作答是「{quoted}」，未体现「{expect}」"


def _first_error(steps: list[dict], verdict: str) -> dict | None:
    """第一个未覆盖的步骤；完全无关时固定指向第 1 步（从第一步就没对上）。"""
    missing = [step for step in steps if not step["found"]]
    if not missing:
        return None
    step = steps[0] if verdict == VERDICT_WRONG else missing[0]
    note = step["note"]
    tail = note[len("缺失：") :] if note.startswith("缺失：") else note
    if tail.startswith("这一步该得到"):
        reason = tail
    else:
        reason = f"这一步应为「{step['expect']}」，{tail}"
    return {"step": step["index"], "reason": reason}


def _better_method(topic: str, plain_answer: str) -> tuple[dict, str]:
    """更优解法 + 对比文案。

    mock 只说「推荐什么、几步、为什么」，不评价学生的方法（那需要真正的理解）。
    学生作答里确实出现了推荐解法的特征标记时，才补一句肯定。
    """
    spec = BETTER_METHODS[topic]
    method = {
        "name": spec["name"],
        "why": spec["why"],
        "steps": list(spec["steps"]),
    }
    if any(keyword.lower() in plain_answer for keyword in spec["signature"]):
        comparison = f"你用的就是推荐解法（{spec['name']}），保持这个习惯。"
    else:
        comparison = f"推荐用{spec['name']}：{spec['better_steps']} 步，{spec['why']}"
    return method, comparison


def _keyword_hit(plain_answer: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in plain_answer for keyword in keywords)


def _key_form_hit(plain_answer: str, plain_expected: str) -> bool:
    """答案里直接抄了标准答案的关键式（如把 (2,-1) 写在推导中间）。"""
    return any(
        form in plain_answer
        for form in answer_forms(plain_expected)
        if len(form) >= KEY_FORM_MIN_CHARS
    )


def _review_topic(question: dict) -> str:
    """判分用的题型；a 退化（不是二次函数）时退回通用文案。"""
    stem = str(question.get("stem_latex") or question.get("stem") or "")
    topic = _topic(stem)
    if topic in AXIS_TOPICS and axis_of(*coefficients(stem)[:2]) is None:
        return "fallback"
    return topic


def mock_review(question: dict, user_answer: str) -> dict:
    """确定性 mock 批改：分数由规则算，points/issues/suggestion 按题型给文案。"""
    reference = str(question.get("answer", ""))
    plain_expected = normalize(reference)
    plain_answer = normalize(user_answer)
    topic = _review_topic(question)

    value_hit = bool(plain_answer) and (
        judge(question, user_answer) or _key_form_hit(plain_answer, plain_expected)
    )

    points: list[dict] = []
    for spec in TOPIC_POINTS[topic]:
        hit = _keyword_hit(plain_answer, spec.get("keywords", ()))
        if not hit and spec.get("need_value"):
            hit = value_hit
        points.append({"hit": bool(hit), "text": spec["text"]})

    hits = sum(1 for point in points if point["hit"])
    exact = bool(plain_answer) and plain_answer == plain_expected

    if exact:
        score, verdict = EXACT_SCORE, VERDICT_CORRECT
        # 满分不列扣分点：结果完全一致时不再要求补过程（过程分只影响 85 档）
        points = [{**point, "hit": True} for point in points]
        hits = len(points)
    elif value_hit:
        score, verdict = EQUIVALENT_SCORE, VERDICT_CORRECT
    elif hits:
        score = min(PARTIAL_MAX, PARTIAL_BASE + PARTIAL_STEP * (hits - 1))
        verdict = VERDICT_PARTIAL
    else:
        marks = sum(1 for mark in RELEVANT_MARKS if mark in plain_answer)
        score = min(WRONG_MAX, WRONG_BASE + WRONG_STEP * marks)
        verdict = VERDICT_WRONG

    issues = [
        spec["issue"]
        for spec, point in zip(TOPIC_POINTS[topic], points)
        if not point["hit"]
    ]
    if verdict == VERDICT_WRONG:
        issues.append(f"整段作答看不出与本题的关系，建议回看卡片《{TOPIC_CARD[topic]}》")
    if not plain_answer:
        issues.insert(0, "没有作答")

    # 逐步诊断：先按 solution 切步（不足 2 步用题型模板补），再逐步比对作答
    answer_numbers = _numbers(plain_answer)
    steps = []
    for index, expect in enumerate(_expectations(question, topic), start=1):
        found = exact or _step_found(expect, plain_answer, answer_numbers)
        steps.append(
            {
                "index": index,
                "expect": expect,
                "found": found,
                "note": _step_note(expect, found, plain_answer, user_answer),
            }
        )
    better_method, method_comparison = _better_method(topic, plain_answer)

    advice = TOPIC_ADVICE[topic]
    return {
        "score": score,
        "verdict": verdict,
        "points": points,
        "issues": issues,
        "suggestion": advice["full"] if score >= EQUIVALENT_SCORE else advice["worse"],
        "reference": reference,
        "steps": steps,
        "first_error": _first_error(steps, verdict),
        "better_method": better_method,
        "method_comparison": method_comparison,
    }

