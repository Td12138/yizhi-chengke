"""判分归一化与等价规则：纯函数、无依赖，供 orchestrator 与 mock 批改共用。

放在独立模块是为了让 `providers/fixtures.py` 也能复用同一套等价规则，
而不必反向依赖 `orchestrator`（那会形成 orchestrator → agents → llm → fixtures → orchestrator 的循环）。
"""

from __future__ import annotations

import re
import unicodedata

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


def normalize(text: str) -> str:
    """判分归一化：NFKC 全角转半角（含 −），去掉空白、$、括号、反斜杠并统一小写。"""
    text = unicodedata.normalize("NFKC", str(text)).translate(FULLWIDTH_TABLE)
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


def judge(question: dict, user_answer: str) -> bool:
    """判分：fill 比较等价写法，choice 只比首字母。"""
    expected = normalize(question.get("answer", ""))
    given = normalize(user_answer)
    if question.get("qtype") == "choice":
        return bool(given) and bool(expected) and given[0] == expected[0]
    if not expected or not given:
        return False
    return any(
        same_value(expect_form, given_form)
        for expect_form in answer_forms(expected)
        for given_form in answer_forms(given)
    )
