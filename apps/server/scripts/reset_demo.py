"""清空演示数据：卡片、题目、作答、事件全部重置，保留表结构。

原图已经按识别结果重命名归档，默认**不删** `data/uploads/` 里的文件；
确实要连图一起清掉时显式加 `--purge-images`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db  # noqa: E402

TABLES = ("agent_event", "attempt", "question", "card", "source")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清空演示数据（默认保留归档原图）")
    parser.add_argument(
        "--purge-images",
        action="store_true",
        help="同时删除 data/uploads/ 下的所有原图（归档图也删，谨慎）",
    )
    return parser.parse_args(argv)


def upload_images() -> list[Path]:
    """上传目录下的文件清单（只清文件，不动子目录）。"""
    if not config.UPLOAD_DIR.exists():
        return []
    return [path for path in config.UPLOAD_DIR.iterdir() if path.is_file()]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config.ensure_dirs()
    db.init_db()

    with db.connect() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")

    images = upload_images()
    if args.purge_images:
        for image in images:
            image.unlink(missing_ok=True)
        print(f"已清空：{', '.join(TABLES)}；已删除 {len(images)} 张原图")
    else:
        print(
            f"已清空：{', '.join(TABLES)}；保留 {len(images)} 张原图"
            "（要连图一起删请加 --purge-images）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
