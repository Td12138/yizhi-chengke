"""受限数学解析：模型输出一律先过白名单，再交给 sympy。

`sympify` 会把输入当 Python 代码 eval——`sympify("__import__('os').getcwd()")`
会真的执行、`open(...)` 会真的写文件。所以这里不给裸 eval 的机会：

1. 字符白名单：只允许数字、四则运算、幂、括号、逗号、等号、空格；
2. 标识符白名单：单个字母当变量，长名字只能是已知函数；
3. 解析统一走 `parse_expr`，显式传空 `__builtins__` 与最小 local_dict；
4. 代入值只接受数值，字符串必须能直接转 float（不接受表达式）。
"""

from __future__ import annotations

import math
import re
from typing import Any

import sympy as sp

# 允许出现的字符：数字、四则运算、幂、括号、逗号、等号、空格
ALLOWED_CHARS = re.compile(r"^[0-9A-Za-z.+\-*/^(),= ]+$")
# 连续字母：长度 1 当变量，长度 > 1 必须是已知函数
NAME_RUNS = re.compile(r"[A-Za-z]+")
# 表达式长度上限：正常验算式远小于这个数
MAX_EXPR_CHARS = 300
# 幂指数上限：`9**9**9` 这类指数塔会在求值时把内存/CPU 吃光，先看结构再拒绝
MAX_POW_EXPONENT = 1000
# 幂节点数上限：短表达式也能堆出几十层嵌套幂，超过即视为构造攻击
# 只限制「幂叠幂」；普通多项式可能有几十项 x**n，给到 50 避免误伤
MAX_POW_NODES = 50

# 允许的常量名 → sympy 值（pi/e 是三角/指数题最自然的写法）
# 注意：不把 e 当常量，e 在代数题里常作变量；只放行 pi / PI。
ALLOWED_CONSTANTS: dict[str, Any] = {
    "pi": sp.pi,
    "PI": sp.pi,
}

# 允许的函数名 → sympy 实现（白名单外的标识符一律拒绝）
ALLOWED_FUNCTIONS: dict[str, Any] = {
    "sqrt": sp.sqrt,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "abs": sp.Abs,
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
}

# eval 时只暴露这些名字：没有 __import__、没有 open、没有内置函数
# （evaluate=False 时 sympy 会把 `9**9` 改写成显式 Pow/Mul/Add 构造，需放行这几个结构类）
GLOBAL_DICT: dict[str, Any] = {
    "__builtins__": {},
    "Symbol": sp.Symbol,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Pow": sp.Pow,
    "Mul": sp.Mul,
    "Add": sp.Add,
    "Rational": sp.Rational,
}


class UnsafeInput(ValueError):
    """输入没过白名单：模型给出的表达式 / 代入值不可信。"""


def _check_powers(expr: sp.Expr) -> None:
    """拒绝指数爆炸：指数必须是有限数、不叠幂，且绝对值不超过上限。

    必须在 `evaluate=False` 下检查——`9**9**9` 一旦进入求值阶段就会先算
    9**9 再算 9**(9**9)，内存直接被打满，连异常都来不及抛。
    `((2**1000)**1000)**1000` 每一层指数都合法，但复合后是 2^10^9，必须在
    结构上直接拒绝「base 本身还是 Pow」的叠幂。
    """
    if not isinstance(expr, sp.Expr):
        raise UnsafeInput("表达式不是数学式")
    powers = expr.atoms(sp.Pow)
    if len(powers) > MAX_POW_NODES:
        raise UnsafeInput(f"幂运算过多（{len(powers)} 个）")
    for power in powers:
        # 只拒绝「底数本身直接是一个 Pow」的指数塔；sqrt((-5)**2 - 24) 的
        # base 是 Add，不应误伤。
        if isinstance(power.base, sp.Pow):
            raise UnsafeInput("不允许幂叠幂（指数塔）")
        try:
            exponent = float(power.exp)
        except (TypeError, ValueError) as exc:
            raise UnsafeInput("幂指数不是有限数") from exc
        if not math.isfinite(exponent):
            raise UnsafeInput("幂指数不是有限数")
        if abs(exponent) > MAX_POW_EXPONENT:
            raise UnsafeInput(
                f"幂指数过大（{exponent:g}），最多允许 {MAX_POW_EXPONENT}"
            )


def parse_expression(text: str) -> sp.Expr:
    """白名单校验后做受限解析；非法输入抛 UnsafeInput，语法错误抛原异常。"""
    raw = str(text or "").strip()
    if not raw:
        raise UnsafeInput("表达式为空")
    if len(raw) > MAX_EXPR_CHARS:
        raise UnsafeInput("表达式过长")
    if not ALLOWED_CHARS.match(raw):
        raise UnsafeInput("表达式含非法字符")
    allowed_names = {**ALLOWED_FUNCTIONS, **ALLOWED_CONSTANTS}
    for name in NAME_RUNS.findall(raw):
        if len(name) > 1 and name not in allowed_names:
            raise UnsafeInput(f"表达式含未知标识符：{name}")
    # ^ 是数学写法，Python/sympy 里是异或；白名单只放行了 ^，这里显式换掉
    code = raw.replace("^", "**")
    expr = sp.parse_expr(
        code,
        global_dict=dict(GLOBAL_DICT),
        local_dict=allowed_names,
        # 先只建语法树不求值：指数爆炸要在结构层面拦，不能交给 sympy 算
        evaluate=False,
    )
    _check_powers(expr)
    return expr


def parse_subs(raw_subs: Any) -> dict[sp.Symbol, sp.Expr]:
    """代入值：key 只接受单个字母，value 只接受数值（或可转 float 的字符串）。"""
    if raw_subs in (None, ""):
        return {}
    if not isinstance(raw_subs, dict):
        raise UnsafeInput("subs 必须是对象")
    subs: dict[sp.Symbol, sp.Expr] = {}
    for key, value in raw_subs.items():
        name = str(key).strip()
        if len(name) != 1 or not name.isascii() or not name.isalpha():
            raise UnsafeInput(f"subs 变量名必须是单个字母：{name}")
        subs[sp.Symbol(name)] = _parse_number(value)
    return subs


def _parse_number(value: Any) -> sp.Expr:
    """只接受数值：bool / 表达式字符串 / 容器一律拒绝。"""
    if isinstance(value, bool):
        raise UnsafeInput("subs 的值不支持布尔值")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise UnsafeInput(f"subs 的值不是数值：{text[:20]}") from exc
    else:
        raise UnsafeInput(f"subs 的值类型不支持：{type(value).__name__}")
    if not math.isfinite(number):
        raise UnsafeInput("subs 的值不是有限数")
    if isinstance(value, int):
        return sp.Integer(value)
    return sp.Float(number)
