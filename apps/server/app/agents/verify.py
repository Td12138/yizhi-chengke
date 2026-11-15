"""质检 Agent：用 sympy 代入验算，拦下答案不自洽的题。

这是多 Agent 存在的核心理由——生成式命题必须能被独立验算否定。

安全边界：`verify` 是模型输出，绝不能直接过 `sympify`（那是 eval）。
解析统一走 `safe_math`：字符白名单 + 受限 `parse_expr` + 只收数值的 subs。
"""

from __future__ import annotations

import re

import sympy as sp

from .. import safe_math
from ..safe_math import UnsafeInput
from .base import Agent

# 数值比较容差
TOLERANCE = 1e-6


def check_question(question: dict) -> tuple[bool, str]:
    """有 verify 就验算，没有就标记「无可验算表达式」（不阻塞）。"""
    verify = question.get("verify")
    # None / "" / [] / {} / 0 都是「没有可验算表达式」，统一走结构检查；
    # 只有非空且不是对象（如 "x**2"、[1,2]、42）才是坏题。
    if not verify:
        return True, "无可验算表达式，仅做结构检查"
    if not isinstance(verify, dict):
        return False, "verify 不是对象"

    expr_text = str(verify.get("expr", "")).strip()
    raw_subs = verify.get("subs") or {}
    try:
        expr = safe_math.parse_expression(expr_text)
        subs = safe_math.parse_subs(raw_subs)
        value = float(sp.N(expr.subs(subs)))
    except UnsafeInput as exc:  # 白名单拒绝：原样说明原因
        return False, str(exc)
    except Exception as exc:  # 语法错误 / 无法求值：同样视为不通过
        return False, f"表达式无法解析：{exc}"

    try:
        expect = float(verify.get("expect", 0.0))
    except (TypeError, ValueError, OverflowError):
        # expect 是 "C" / None / 10**400 时不能让整批发题中断
        return False, f"期望值不是数值：{verify.get('expect')!r}"

    passed = abs(value - expect) <= TOLERANCE
    detail = f"{expr_text} @ {raw_subs or '(无代入)'} = {value:g}，期望 {expect:g}"
    return passed, detail


def brief(stem: str, width: int = 18) -> str:
    text = re.sub(r"\$[^$]*\$", "…", stem).strip()
    return text[:width] + ("…" if len(text) > width else "")


class VerifyAgent(Agent):
    name = "verify"

    async def run(self) -> list[bool]:
        questions = self.ctx.state.get("questions", [])
        await self.emit("running", f"逐题验算 {len(questions)} 道题")

        results: list[bool] = []
        for question in questions:
            if not isinstance(question, dict):
                item = {}
                passed, detail = False, "题目不是对象"
            else:
                item = question
                try:
                    passed, detail = check_question(item)
                except Exception as exc:  # 单题坏掉只记驳回，不拖垮整批
                    passed, detail = False, f"验算异常：{type(exc).__name__}: {exc}"
                question["verified"] = passed
                question["verify_log"] = detail
            results.append(passed)
            await self.emit(
                "delta",
                f"「{brief(item.get('stem_latex', ''))}」"
                f"{'通过' if passed else '驳回'}：{detail}",
                question=item.get("stem_latex", ""),
                ok=passed,
            )

        failed = sum(1 for r in results if not r)
        if failed:
            await self.emit("rejected", f"{failed} 道题未通过验算，打回命题 Agent 重生成")
        else:
            await self.emit("done", f"{len(results)} 道题全部通过验算")
        return results
