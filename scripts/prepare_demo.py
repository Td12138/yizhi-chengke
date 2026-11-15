"""把本地库准备成「可录制」状态：清库 → 入库一张样例板书 → 生成一套练习卷。

用法（在仓库根执行，无需先起服务）：
    <python> scripts/prepare_demo.py
    <python> scripts/prepare_demo.py --keep-images   # 保留已归档的原图

为什么要有这个脚本：录屏前手工点一遍容易漏步骤，而且卡片数量、题目数量每次不同。
一条命令把状态固定下来，重录时结果一致。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server"))

from app import config, db  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.orchestrator import Orchestrator, new_run_id  # noqa: E402

# 演示用的样例图与出题需求
SAMPLE_DIR = ROOT / "apps" / "web" / "public" / "samples"
DEMO_IMAGES = [
    SAMPLE_DIR / "sample-quadratic.png",
    SAMPLE_DIR / "sample-equation.png",
    SAMPLE_DIR / "sample-trig.png",
]
DEMO_REQUIREMENT = "给我出 5 道这页的变式题，中等难度"
DEMO_COUNT = 5
TABLES = ("agent_event", "attempt", "question", "card", "source")


async def prepare(purge_images: bool) -> None:
    config.ensure_dirs()
    db.init_db()

    with db.connect() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")

    if purge_images:
        removed = 0
        for image in config.UPLOAD_DIR.glob("*"):
            image.unlink(missing_ok=True)
            removed += 1

    bus = EventBus()
    orchestrator = Orchestrator(bus)

    # 三张样例各入库一次：mock 按文件名分流到不同知识点集，
    # 复制到上传目录再识别，归档改名只作用于副本
    for sample in DEMO_IMAGES:
        staged = config.UPLOAD_DIR / f"demo-{uuid.uuid4().hex[:8]}{sample.suffix}"
        shutil.copyfile(sample, staged)
        run_id = new_run_id()
        bus.create(run_id)
        await orchestrator.ingest(run_id, staged, source_name=sample.name)
        if bus.get(run_id).error:
            raise SystemExit(f"入库失败（{sample.name}）：{bus.get(run_id).error}")

    cards = db.list_cards()
    run_id = new_run_id()
    bus.create(run_id)
    await orchestrator.generate(
        run_id, [card["id"] for card in cards], DEMO_REQUIREMENT, DEMO_COUNT
    )
    if bus.get(run_id).error:
        raise SystemExit(f"出题失败：{bus.get(run_id).error}")

    questions = db.list_questions()
    verified = sum(1 for question in questions if question.get("verified"))
    rejects = sum(
        1
        for event in bus.get(run_id).history
        if event.agent == "verify" and event.phase == "rejected"
    )

    print(f"卡片 {len(cards)} 张，题目 {len(questions)} 道（验算通过 {verified}）")
    print(f"质检驳回 {rejects} 轮")
    if purge_images:
        print(f"已清空旧原图 {removed} 张")
    if questions:
        print(f"建议演示用的错题：#{questions[0]['id']}，输入错误答案如 `-2` 可触发讲解")
    print("\n下一步：起后端与前端，打开 http://127.0.0.1:5173 开始录制")


def main() -> None:
    parser = argparse.ArgumentParser(description="准备演示数据")
    parser.add_argument(
        "--keep-images", action="store_true", help="保留已归档的原图（默认清空）"
    )
    args = parser.parse_args()
    asyncio.run(prepare(purge_images=not args.keep_images))


if __name__ == "__main__":
    main()
