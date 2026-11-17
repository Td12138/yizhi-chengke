"""质检 Agent：用 sympy 代入验算，拦下答案不自洽的题。

这是多 Agent 存在的核心理由——生成式命题必须能被独立验算否定。

安全边界：`verify` 是模型输出，绝不能直接过 `sympify`（那是 eval）。
解析统一走 `safe_math`：字符白名单 + 受限 `parse_expr` + 只收数值的 subs。

验算分三级（v0.1.5 起，消除「无 verify 却标已验算」的误导）：

* `symbolic`：带 verify，sympy 代入验算；
* `structural`：无 verify，做题干/答案/题型结构检查（开关 VERIFY_STRUCTURE_CHECK）；
* `unchecked`：开关关闭时的旧行为——无 verify 直接放行，UI 如实标「未验算」。
"""

from __future__ import annotations

import re

import sympy as sp

from .. import config, safe_math
from ..safe_math import UnsafeInput
from .base import Agent

# 数值比较容差
TOLERANCE = 1e-6

# 验算级别：写进 question["verify_level"]，前端按级别显示不同徽章
LEVEL_SYMBOLIC = "symbolic"
LEVEL_STRUCTURAL = "structural"
LEVEL_UNCHECKED = "unchecked"

# 结构检查：题干最短长度。只拦空/单字符的残缺题干，
# 「解方程」这类 3 字短题干是合法题，不能误伤（审查 P2-15）。
MIN_STEM_CHARS = 2
# 选择题最少选项数
MIN_OPTIONS = 2
# 允许的题型
QTYPES = ("fill", "choice")


def structural_check(question: dict) -> tuple[bool, str]:
    """无 verify 时的结构检查：题干、答案、题型三者完整且自洽。

    只查结构，不判内容对错——内容正确性在没有可验算表达式时无法保证，
    所以通过的题标 `structural` 而不是 `symbolic`，UI 不称其「已验算」。
    """
    stem = str(question.get("stem_latex") or "").strip()
    if len(stem) < MIN_STEM_CHARS:
        return False, "结构检查未通过：题干为空或过短"
    qtype = str(question.get("qtype") or "fill").strip().lower()
    if qtype not in QTYPES:
        return False, f"结构检查未通过：未知题型 {qtype}"
    answer = str(question.get("answer") or "").strip()
    if not answer:
        return False, "结构检查未通过：答案为空"
    if qtype == "choice":
        options = question.get("options")
        if not isinstance(options, list) or len(options) < MIN_OPTIONS:
            return False, "结构检查未通过：选择题选项不足 2 个"
        texts = [str(option).strip() for option in options]
        letter = answer[0].upper()
        in_range = "A" <= letter <= chr(ord("A") + len(options) - 1)
        if not in_range and answer not in texts:
            return False, "结构检查未通过：答案不在选项范围内"
    return True, "无可验算表达式，结构检查通过：题干、答案与题型完整"


def _is_format_issue(detail: str) -> bool:
    """白名单拒绝里，哪些属于「模型格式没写对」而不是「疑似注入」。

    只放行两类：多字母标识符（如 ``ac`` 想写 ``a*c``）、表达式不是数学式。
    含非法字符、subs 非数值、超长等情况一律保持拒绝——那是安全边界。
    """
    return detail.startswith("表达式含未知标识符") or detail.startswith("表达式不是数学式")


def check_question_detailed(question: dict) -> tuple[bool, str, str]:
    """返回 (是否通过, 说明, 验算级别)。级别取 LEVEL_* 常量。"""
    verify = question.get("verify")
    # None / "" / [] / {} / 0 都是「没有可验算表达式」，统一走结构检查；
    # 只有非空且不是对象（如 "x**2"、[1,2]、42）才是坏题。
    if not verify:
        if config.VERIFY_STRUCTURE_CHECK:
            passed, detail = structural_check(question)
            return passed, detail, LEVEL_STRUCTURAL
        # 开关关闭 = 旧行为：直接放行，但级别如实记 unchecked
        return True, "无可验算表达式，仅做结构检查", LEVEL_UNCHECKED
    if not isinstance(verify, dict):
        return False, "verify 不是对象", LEVEL_SYMBOLIC

    expr_text = str(verify.get("expr", "")).strip()
    raw_subs = verify.get("subs") or {}
    try:
        expr = safe_math.parse_expression(expr_text)
        subs = safe_math.parse_subs(raw_subs)
        value = float(sp.N(expr.subs(subs)))
    except UnsafeInput as exc:  # 白名单拒绝：原样说明原因
        detail = str(exc)
        if config.VERIFY_DEGRADE_FORMAT and _is_format_issue(detail):
            # 模型写不好校验式（多字母变量、LaTeX 残留）不是「答案错」，
            # 降级为结构检查并如实标注，避免真实模型下整批被格式问题打死。
            passed, note = structural_check(question)
            return passed, f"{detail}；降级为结构检查：{note}", LEVEL_STRUCTURAL
        return False, detail, LEVEL_SYMBOLIC
    except Exception as exc:  # 语法错误 / 无法求值
        detail = f"表达式无法解析：{exc}"
        if config.VERIFY_DEGRADE_FORMAT:
            passed, note = structural_check(question)
            return passed, f"{detail}；降级为结构检查：{note}", LEVEL_STRUCTURAL
        return False, detail, LEVEL_SYMBOLIC

    try:
        expect = float(verify.get("expect", 0.0))
    except (TypeError, ValueError, OverflowError):
        # expect 是 "C" / None / 10**400 时不能让整批发题中断
        detail = f"期望值不是数值：{verify.get('expect')!r}"
        if config.VERIFY_DEGRADE_FORMAT:
            passed, note = structural_check(question)
            return passed, f"{detail}；降级为结构检查：{note}", LEVEL_STRUCTURAL
        return False, detail, LEVEL_SYMBOLIC

    passed = abs(value - expect) <= TOLERANCE
    detail = f"{expr_text} @ {raw_subs or '(无代入)'} = {value:g}，期望 {expect:g}"
    return passed, detail, LEVEL_SYMBOLIC


def check_question(question: dict) -> tuple[bool, str]:
    """二级接口（历史调用方不变）：只要通过与否和说明，级别另取 detailed。"""
    passed, detail, _level = check_question_detailed(question)
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
        levels: dict[str, int] = {}
        for question in questions:
            if not isinstance(question, dict):
                item = {}
                passed, detail, level = False, "题目不是对象", LEVEL_STRUCTURAL
            else:
                item = question
                try:
                    passed, detail, level = check_question_detailed(item)
                except Exception as exc:  # 单题坏掉只记驳回，不拖垮整批
                    passed, detail = False, f"验算异常：{type(exc).__name__}: {exc}"
                    level = LEVEL_SYMBOLIC
                question["verified"] = passed
                question["verify_log"] = detail
                question["verify_level"] = level
            levels[level] = levels.get(level, 0) + 1
            results.append(passed)
            await self.emit(
                "delta",
                f"「{brief(item.get('stem_latex', ''))}」"
                f"{'通过' if passed else '驳回'}：{detail}",
                question=item.get("stem_latex", ""),
                ok=passed,
                verify_level=level,
            )

        failed = sum(1 for r in results if not r)
        if failed:
            await self.emit("rejected", f"{failed} 道题未通过验算，打回命题 Agent 重生成")
        else:
            await self.emit(
                "done", f"{len(results)} 道题全部通过验算", levels=levels
            )
        return results
