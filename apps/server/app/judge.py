"""判分归一化与等价规则：纯函数、不碰数据库，供 orchestrator 与 mock 批改共用。

放在独立模块是为了让 `providers/fixtures.py` 也能复用同一套等价规则，
而不必反向依赖 `orchestrator`（那会形成 orchestrator → agents → llm → fixtures → orchestrator 的循环）。

v0.1.5 起追加 sympy 等价判定（开关 JUDGE_SYMPY_EQUIV）：字符串/数值比对失败后，
把两侧答案做轻量 LaTeX → 纯文本转换，过 safe_math 白名单解析，simplify 差为 0 即等价。
解析失败一律退回旧规则的结果，绝不因 sympy 异常把判分搞挂。
"""

from __future__ import annotations

import re
import unicodedata

import sympy as sp

from . import config, safe_math

# 中文输入法常见的全角标点/符号，判分前统一成半角（逗号要保留，不能直接删）
FULLWIDTH_TABLE = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "，": ",",
        "：": ":",
        "；": ";",
        "。": ".",
        "！": "!",
        "？": "?",
        "【": "[",
        "】": "]",
        "－": "-",
        "−": "-",
        "＋": "+",
        "＝": "=",
        "．": ".",
        "／": "/",
        "＊": "*",
        "＾": "^",
        **{chr(0xFF10 + digit): str(digit) for digit in range(10)},
    }
)

# 纯数值答案：判分时按数值比较，容忍 2 与 2.0、＋2 这类写法差异
NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
# 数值比较容差（浮点写法差异，不是评分宽松度）
NUMBER_TOLERANCE = 1e-9
# 归一化时整体删掉的字符：空白、$ 与 LaTeX 分组/转义符
NOISE_PATTERN = re.compile(r"[\s$\\({})\\\\]")


# 上标数字/符号：NFKC 会先把 x² 变成 x2，指数信息就丢了；必须提前换成 ^ 形式
SUPERSCRIPT_TABLE = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "⁺": "+",
    "⁻": "-",
}
SUPERSCRIPT_PATTERN = re.compile("[" + "".join(SUPERSCRIPT_TABLE) + "]+")


def expand_superscripts(text: str) -> str:
    """把上标串换成 `^` 形式：`x²` 与 `x^2`、`10⁻¹` 与 `10^-1` 视为同一种写法。"""
    return SUPERSCRIPT_PATTERN.sub(
        lambda match: "^" + "".join(SUPERSCRIPT_TABLE[ch] for ch in match.group()),
        text,
    )


def normalize(text: str) -> str:
    """判分归一化：上标展开 → NFKC 全角转半角（含 −），去掉空白、$、括号、反斜杠并统一小写。"""
    text = expand_superscripts(str(text))
    text = unicodedata.normalize("NFKC", text).translate(FULLWIDTH_TABLE)
    return NOISE_PATTERN.sub("", text).lower()


def answer_forms(normalized: str) -> set[str]:
    """一个已归一化答案的等价写法集合。

    有等号时把等号右侧也算一种写法：标准答案 `x = 2` 与用户只填 `2` 等价。
    取最后一个等号，兼容 `a = b = 2` 这类连等式。
    """
    if not normalized:
        return set()
    forms = {normalized}
    if "=" in normalized:
        right = normalized.rsplit("=", 1)[1]
        if right:
            forms.add(right)
    return forms


def same_value(left: str, right: str) -> bool:
    """两种写法是否是同一个值：先比字符串，再按数值容差比。"""
    if left == right:
        return True
    if NUMBER_PATTERN.fullmatch(left) and NUMBER_PATTERN.fullmatch(right):
        return abs(float(left) - float(right)) <= NUMBER_TOLERANCE
    return False


# ---- sympy 等价判定（v0.1.5）----

# LaTeX 排版噪声：\left \right \, \; \! \quad 等，不影响数学含义
LATEX_NOISE = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])|\\[,;! ]|\\q?quad")
# \frac{a}{b} / \dfrac{a}{b}：只收单层花括号，嵌套靠循环展开
FRAC_PATTERN = re.compile(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
SQRT_PATTERN = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
# 常见函数命令 → 白名单函数名
FUNC_COMMAND = re.compile(r"\\(sin|cos|tan|log|ln|exp|abs)(?![A-Za-z])")
# 嵌套展开轮数上限：正常答案远不到 5 层
MAX_LATEX_DEPTH = 5

# 隐式乘法补 *：sympy 的 evaluate=False 与隐式乘法变换不兼容（会拼出非法 AST），
# 必须在解析前显式补上。三条规则：数字后接字母/左括号、右括号后接字母数字左括号、
# 单字母变量后接左括号（多字母函数名 sin( 不受影响）。
IMPLICIT_DIGIT = re.compile(r"(\d)([A-Za-z(])")
IMPLICIT_PAREN = re.compile(r"(\))([A-Za-z0-9(])")
IMPLICIT_VAR = re.compile(r"(?<![A-Za-z])([A-Za-z])(\()")


def insert_implicit_mul(text: str) -> str:
    text = IMPLICIT_DIGIT.sub(r"\1*\2", text)
    text = IMPLICIT_PAREN.sub(r"\1*\2", text)
    return IMPLICIT_VAR.sub(r"\1*\2", text)


def latex_to_plain(text: str) -> str:
    """LaTeX → 可解析纯文本：只转常见记号，未知命令原样保留。

    未知命令（如 \\alpha）不删——删掉会改变数学含义造成误判；
    保留则白名单拒绝解析，安全退回字符串规则。
    """
    text = expand_superscripts(str(text))
    text = unicodedata.normalize("NFKC", text).translate(FULLWIDTH_TABLE)
    text = text.replace("$", "")
    text = LATEX_NOISE.sub("", text)
    text = text.replace(r"\cdot", "*").replace(r"\times", "*").replace(r"\div", "/")
    text = text.replace(r"\pi", "pi")
    text = FUNC_COMMAND.sub(r"\1", text)
    for _ in range(MAX_LATEX_DEPTH):
        expanded = FRAC_PATTERN.sub(r"((\1)/(\2))", text)
        expanded = SQRT_PATTERN.sub(r"sqrt(\1)", expanded)
        if expanded == text:
            break
        text = expanded
    text = text.replace("{", "(").replace("}", ")").strip()
    return insert_implicit_mul(text)


def _parse_side(text: str) -> sp.Expr | None:
    """单侧表达式过白名单解析；任何失败都返回 None（退回旧规则）。"""
    try:
        return safe_math.parse_expression(text)
    except Exception:
        return None


def _equation_side(text: str) -> tuple[sp.Expr | None, bool]:
    """有等号取最后一个等号两侧之差（移项），无等号直接解析。"""
    if "=" in text:
        lhs, rhs = text.rsplit("=", 1)
        left, right = _parse_side(lhs), _parse_side(rhs)
        if left is None or right is None:
            return None, True
        return left - right, True
    return _parse_side(text), False


def sympy_equivalent(answer: str, given: str) -> bool:
    """两种写法是否数学等价：simplify(差) == 0；方程还允许整体反号。

    只在字符串/数值比对失败后调用；任何解析/化简异常都按「不等价」处理，
    判分结果不会比旧规则更宽松地出错——白名单外的输入根本进不了 sympy。
    """
    left, left_is_eq = _equation_side(latex_to_plain(answer))
    right, right_is_eq = _equation_side(latex_to_plain(given))
    if left is None or right is None:
        return False
    try:
        if left_is_eq != right_is_eq:
            # 一侧是方程、一侧是纯数：把数代入方程验证（x=2 与 4/2）。
            # 纯表达式（含变量）不算方程的解：x=2 与 x-2 不能判等。
            expr_side, num_side = (left, right) if left_is_eq else (right, left)
            if num_side.free_symbols:
                return False
            variables = expr_side.free_symbols
            if len(variables) != 1:
                return False
            (var,) = variables
            return sp.simplify(expr_side.subs(var, num_side)) == 0
        if sp.simplify(left - right) == 0:
            return True
        # x - 2 与 2 - x 解集相同：两侧都是方程时允许反号
        if left_is_eq and right_is_eq and sp.simplify(left + right) == 0:
            return True
    except Exception:
        return False
    return False


def judge(question: dict, user_answer: str) -> bool:
    """判分：fill 比较等价写法（字符串 → 数值 → sympy 等价），choice 只比首字母。"""
    expected = normalize(question.get("answer", ""))
    given = normalize(user_answer)
    if question.get("qtype") == "choice":
        return bool(given) and bool(expected) and given[0] == expected[0]
    if not expected or not given:
        return False
    if config.JUDGE_SYMPY_EQUIV:
        # 两侧都是方程时不做「右端值」交叉比对：x=2 与 y=2 右端同为 2 但不是同一答案
        both_equations = "=" in expected and "=" in given
        forms_match = (
            same_value(expected, given)
            if both_equations
            else any(
                same_value(expect_form, given_form)
                for expect_form in answer_forms(expected)
                for given_form in answer_forms(given)
            )
        )
        if forms_match:
            return True
        return sympy_equivalent(question.get("answer", ""), user_answer)
    return any(
        same_value(expect_form, given_form)
        for expect_form in answer_forms(expected)
        for given_form in answer_forms(given)
    )
