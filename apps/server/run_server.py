"""桌面 sidecar 入口：读取环境变量后启动 uvicorn。

Web 开发模式请继续用 `python -m uvicorn app.main:app --port 8710`；
本文件只服务于 Electron 封装（PyInstaller onedir 的入口）。
"""

from __future__ import annotations

import os
import sys

import uvicorn

# PyInstaller 静态分析看不到 uvicorn.run("app.main:app") 的字符串导入，
# 这里显式导入一次，保证整个 app 包（含 agents / providers）进产物。
import app.main  # noqa: F401


def main() -> int:
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8710"))
    log_level = os.environ.get("APP_LOG_LEVEL", "info")
    uvicorn.run("app.main:app", host=host, port=port, log_level=log_level, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
