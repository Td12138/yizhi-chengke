"""质检拦截率评测（v0.1.5）：构造题库喂 check_question_detailed，产出可引用数字。

题库全部是构造数据（不是真实模型输出），回答「验算环节能不能拦住坏题」：
  * correct：三套样例的 15 道正确题（round 2，verify 全部自洽）→ 期望全放行；
  * wrong：同 15 题把 expect 偏移 +1 → 期望全驳回（拦截率）；
  * struct_bad：无 verify 且结构损坏的 5 道题 → 期望全驳回（结构检查）；
  * attack：白名单攻击载荷 3 条 → 期望全驳回（安全边界）。

用法（仓库根执行，无需起服务、无需密钥）：
    <python> scripts/eval_verify.py [--out results.json]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server"))

from app.agents.verify import check_question_detailed  # noqa: E402
from app.providers import fixtures  # noqa: E402

# expect 偏移量：保证与真实值不等（最小差 1 远大于容差 1e-6）
WRONG_EXPECT_OFFSET = 1.0

STRUCT_BAD = [
    {"stem_latex": "", "answer": "2"},
    {"stem_latex": "1 + 1 = ?", "answer": ""},
    {"stem_latex": "1 + 1 = ?", "qtype": "proof", "answer": "2"},
    {"stem_latex": "1 + 1 = ?", "qtype": "choice", "options": ["1"], "answer": "A"},
    {
        "stem_latex": "1 + 1 = ?",
        "qtype": "choice",
        "options": ["1", "2"],
        "answer": "D",
    },
]

ATTACK = [
    {"verify": {"expr": "__import__('os').getcwd()", "subs": {}, "expect": 0}},
    {"verify": {"expr": "((2**1000)**1000)**1000", "subs": {}, "expect": 0}},
    {"verify": {"expr": "9**9**9", "subs": {}, "expect": 0}},
]


def build_banks() -> dict[str, list[dict]]:
    correct: list[dict] = []
    for key in fixtures.SETS:
        correct.extend(fixtures.mock_questions(2, fixtures.SETS[key]["cards"]))
    wrong = copy.deepcopy(correct)
    for question in wrong:
        question["verify"]["expect"] = (
            float(question["verify"]["expect"]) + WRONG_EXPECT_OFFSET
        )
    return {
        "correct": correct,
        "wrong": wrong,
        "struct_bad": copy.deepcopy(STRUCT_BAD),
        "attack": copy.deepcopy(ATTACK),
    }


def evaluate() -> dict:
    banks = build_banks()
    report: dict = {"banks": {}, "constructed": True, "provider": "none（纯规则）"}
    for name, questions in banks.items():
        details = []
        rejected = 0
        for question in questions:
            passed, detail, level = check_question_detailed(question)
            rejected += 0 if passed else 1
            details.append(
                {
                    "stem": str(question.get("stem_latex", ""))[:40],
                    "passed": passed,
                    "level": level,
                    "detail": detail[:120],
                }
            )
        total = len(questions)
        report["banks"][name] = {
            "total": total,
            "rejected": rejected,
            "passed": total - rejected,
            "reject_rate": rejected / total if total else 0.0,
            "details": details,
        }

    wrong = report["banks"]["wrong"]
    correct = report["banks"]["correct"]
    report["headline"] = {
        "拦截率（错答案题被驳回比例）": wrong["reject_rate"],
        "误驳率（正确题被驳回比例）": correct["reject_rate"],
        "结构检查拦截率": report["banks"]["struct_bad"]["reject_rate"],
        "攻击载荷拦截率": report["banks"]["attack"]["reject_rate"],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="质检拦截率评测（构造题库）")
    parser.add_argument("--out", type=Path, default=None, help="结果 JSON 落盘路径")
    args = parser.parse_args(argv)

    report = evaluate()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)

    headline = report["headline"]
    ok = (
        headline["拦截率（错答案题被驳回比例）"] == 1.0
        and headline["误驳率（正确题被驳回比例）"] == 0.0
        and headline["结构检查拦截率"] == 1.0
        and headline["攻击载荷拦截率"] == 1.0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())