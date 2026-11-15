"""质检 Agent：用 sympy 代入验算，拦下答案不自洽的题。

这是多 Agent 存在的核心理由——生成式命题必须能被独立验算否定。
"""

from __future__ import annotations

import re

import sympy as sp

from .base import Agent

# 数值比较容差
TOLERANCE = 1e-6


def check_question(question: dict) -> tuple[bool, str]:
    """有 verify 就验算，没有就标记「无可验算表达式」（不阻塞）。"""
    verify = question.get("verify")
    if not verify:
        return True, "无可验算表达式，仅做结构检查"

    expr_text = str(verify.get("expr", "")).strip()
    try:
        expr = sp.sympify(expr_text)
        raw_subs = verify.get("subs") or {}
        subs = {sp.Symbol(name): sp.nsimplify(value) for name, value in raw_subs.items()}
        value = float(sp.N(expr.subs(subs)))
    except Exception as exc:  # 表达式不可解析即视为不通过
        return False, f"表达式无法解析：{exc}"

    expect = float(verify.get("expect", 0.0))
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
            passed, detail = check_question(question)
            question["verified"] = passed
            question["verify_log"] = detail
            results.append(passed)
            await self.emit(
                "delta",
                f"「{brief(question.get('stem_latex', ''))}」"
                f"{'通过' if passed else '驳回'}：{detail}",
                question=question.get("stem_latex", ""),
                ok=passed,
            )

        failed = sum(1 for r in results if not r)
        if failed:
            await self.emit("rejected", f"{failed} 道题未通过验算，打回命题 Agent 重生成")
        else:
            await self.emit("done", f"{len(results)} 道题全部通过验算")
        return results
