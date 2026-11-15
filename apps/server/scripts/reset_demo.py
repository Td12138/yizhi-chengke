"""清空演示数据：卡片、题目、作答、事件全部重置，保留表结构。

演示前跑一次，避免上一次的卡片和题目堆在界面上。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db  # noqa: E402

TABLES = ("agent_event", "attempt", "question", "card", "source")


def main() -> None:
    config.ensure_dirs()
    db.init_db()
    with db.connect() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
    for image in config.UPLOAD_DIR.glob("*"):
        image.unlink(missing_ok=True)
    print(f"已清空：{', '.join(TABLES)}，并删除上传目录图片")


if __name__ == "__main__":
    main()
