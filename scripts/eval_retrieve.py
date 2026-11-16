"""检索命中率评测（v0.1.5）：20 个问句 × 10 张样例卡片，top-3 命中即算对。

对比两条路径：
  * legacy：2-gram 字段加权（缺省，零依赖）；
  * hybrid：2-gram + 向量 RRF（需 EMBED_BASE_URL / EMBED_MODEL，OpenAI 兼容端点）。

问句含同义改写与英文问句（预期 legacy 召回不到、hybrid 能召回）。
临时库评测，不碰 data/yizhi.db；输出不含端点地址（只记模型名）。

用法（仓库根执行）：
    <python> scripts/eval_retrieve.py --mode legacy
    <python> scripts/eval_retrieve.py --mode both   # 需先配 EMBED_BASE_URL / EMBED_MODEL
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server"))

from app import config, db, retrieval  # noqa: E402
from app.providers import fixtures  # noqa: E402

TOP_K = 3

# (问句, 可接受的卡片标题列表)：命中 top-3 里任意一个即算对
QUERIES: list[tuple[str, list[str]]] = [
    ("二次函数一般式里 a 的作用", ["二次函数的一般式"]),
    ("怎么求抛物线的对称轴", ["顶点式与对称轴"]),
    ("配方法有什么用", ["顶点式与对称轴"]),
    ("抛物线和 x 轴交点个数怎么判断", ["判别式与零点个数"]),
    ("Δ 大于 0 说明什么", ["判别式与零点个数", "根的判别式"]),
    ("方程的根怎么直接算出来", ["求根公式"]),
    ("什么时候方程有两个相等实根", ["根的判别式"]),
    ("十字相乘适合什么方程", ["因式分解法"]),
    ("不解方程求两根之和", ["根与系数的关系"]),
    ("韦达定理怎么用", ["根与系数的关系"]),
    ("sin(pi+α) 怎么化简", ["诱导公式"]),
    ("奇变偶不变是什么意思", ["诱导公式"]),
    ("正弦函数的最小正周期", ["三角函数的图像与周期"]),
    ("ω 对周期有什么影响", ["三角函数的图像与周期"]),
    ("sin(α+β) 展开式", ["两角和与差公式"]),
    ("正余余正口诀对应哪个公式", ["两角和与差公式"]),
    ("把一般式化成顶点式的关键步骤", ["顶点式与对称轴"]),
    ("how to find the vertex of a parabola", ["顶点式与对称轴"]),
    ("quadratic formula for roots", ["求根公式"]),
    ("sine period and amplitude", ["三角函数的图像与周期"]),
]


def _seed_temp_db(tmp: Path) -> None:
    db_path = tmp / "eval-retrieve.db"
    config.DB_PATH = db_path
    db.DB_PATH = db_path
    db.init_db()
    for key, sample in fixtures.SETS.items():
        source_id = db.insert_source(f"eval-{key}.png", sample["blocks"])
        db.insert_cards(source_id, sample["cards"])


def _hit(titles: list[str], expected: list[str]) -> bool:
    return any(title in expected for title in titles)


def run_legacy() -> dict:
    details = []
    hits = 0
    for query, expected in QUERIES:
        cards = db.search_cards(query, TOP_K)
        titles = [card["title"] for card in cards]
        ok = _hit(titles, expected)
        hits += int(ok)
        details.append({"query": query, "expected": expected, "top3": titles, "hit": ok})
    return {
        "mode": "legacy",
        "hit_at_3": hits / len(QUERIES),
        "hits": hits,
        "total": len(QUERIES),
        "details": details,
    }


def run_hybrid() -> dict:
    config.RETRIEVAL_MODE = "hybrid"

    async def _run() -> dict:
        details = []
        hits = 0
        for query, expected in QUERIES:
            cards, note = await retrieval.hybrid_search(query, TOP_K)
            titles = [card["title"] for card in cards]
            ok = _hit(titles, expected)
            hits += int(ok)
            details.append(
                {
                    "query": query,
                    "expected": expected,
                    "top3": titles,
                    "hit": ok,
                    "note": note,
                }
            )
        return {
            "mode": "hybrid",
            "embed_model": config.EMBED_MODEL,
            "hit_at_3": hits / len(QUERIES),
            "hits": hits,
            "total": len(QUERIES),
            "details": details,
        }

    return asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检索 top-3 命中率评测")
    parser.add_argument(
        "--mode",
        choices=["legacy", "hybrid", "both"],
        default="legacy",
        help="hybrid/both 需要 EMBED_BASE_URL 与 EMBED_MODEL 环境变量",
    )
    parser.add_argument("--out", type=Path, default=None, help="结果 JSON 落盘路径")
    args = parser.parse_args(argv)

    if args.mode in ("hybrid", "both") and not (
        os.environ.get("EMBED_BASE_URL") and os.environ.get("EMBED_MODEL")
    ):
        print("hybrid 模式需要 EMBED_BASE_URL 与 EMBED_MODEL 环境变量", file=sys.stderr)
        return 2

    reports = []
    # ignore_cleanup_errors：Windows 下 SQLite WAL 句柄可能延迟释放，清理失败不报错
    with tempfile.TemporaryDirectory(
        prefix="yizhi-eval-", ignore_cleanup_errors=True
    ) as tmp:
        _seed_temp_db(Path(tmp))
        if args.mode in ("legacy", "both"):
            reports.append(run_legacy())
        if args.mode in ("hybrid", "both"):
            reports.append(run_hybrid())
        gc.collect()  # 尽快释放 SQLite 连接句柄，让临时库文件能被清理

    result = {"top_k": TOP_K, "queries": len(QUERIES), "reports": reports}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())