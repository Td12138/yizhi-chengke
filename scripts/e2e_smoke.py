"""端到端冒烟（v0.1.5）：Playwright 跑主路径，验证「照 README 能跑起来」的交付承诺。

覆盖 7 条路径：
  T1 样例上传 → 卡片入库；T2 一句话出题 → 质检驳回回环 → 交付；
  T3 答错 → 判分 → 可视化讲解；T4 追问 → 带出处回答；
  T5 AI 批改 → 流式评语 + 分步清单；T6 人工修正 → 留痕徽章刷新不丢；
  T7 事件落库回放接口（run 结束后仍可取回历史）。

脚本自管服务：临时数据目录 + SERVE_WEB=1 单端口托管，跑完即关，不碰 data/。
依赖：pip install playwright（浏览器版本不符时自动退回本机已装的 chromium）。

用法（仓库根执行，先 npm run build）：
    <python> scripts/e2e_smoke.py [--port 8710] [--shots 截图目录]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "apps" / "server"
WEB_DIST = ROOT / "apps" / "web" / "dist"

HOST = "127.0.0.1"
DEFAULT_PORT = 8710
HEALTH_TIMEOUT_SECONDS = 30
STEP_TIMEOUT_MS = 30_000
VIEWPORT = {"width": 1440, "height": 900}
TOUR_DONE_KEY = "yz.tour.done"

# 演示输入：与 README / 演示脚本一致
SAMPLE_LABEL = "二次函数图象"
REQUIREMENT = "给我出 5 道这页的变式题，中等难度"
WRONG_ANSWER = "1"  # 第 4 题答案是 -1，填 1 必错（演示脚本 T03 同款）
ASK_QUERY = "为什么这里要配方？"
REVIEW_ANSWER = "把x=2代入原式，4-8+3=1"
NEW_CARD_TITLE = "顶点式（E2E 人工修正）"


def _api(port: int, path: str, body: dict | None = None) -> dict:
    url = f"http://{HOST}:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=STEP_TIMEOUT_MS / 1000) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wait_health(port: int) -> None:
    deadline = time.time() + HEALTH_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            health = _api(port, "/api/health")
            if health.get("ready"):
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"后端 {HEALTH_TIMEOUT_SECONDS}s 内未就绪")


def _chromium_executable() -> str | None:
    """playwright 期望的浏览器 revision 不在本机时，退回已装的 chromium 构建。"""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(glob.glob(str(base / "chromium-*" / "chrome-win*" / "chrome.exe")))
    return candidates[-1] if candidates else None


class Smoke:
    def __init__(self, page, port: int, shots: Path | None):
        self.page = page
        self.port = port
        self.shots = shots
        self.passed: list[str] = []

    def shot(self, name: str) -> None:
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.shots / f"{name}.png"), full_page=True)

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  PASS {name}")

    def t1_upload(self) -> None:
        self.page.get_by_role("button", name=SAMPLE_LABEL).click()
        # 卡片逐张流式入库：等数量到齐再断言
        self.page.wait_for_function(
            "() => document.querySelectorAll('.card').length >= 3",
            timeout=STEP_TIMEOUT_MS,
        )
        count = self.page.locator(".card").count()
        assert count >= 3, f"样例应入库 ≥3 张卡片，实际 {count}"
        self.shot("t1-cards")
        self.ok(f"T1 样例上传 → {count} 张卡片")

    def t2_generate(self) -> None:
        self.page.get_by_placeholder("用一句话描述你要的题").fill(REQUIREMENT)
        self.page.locator('[data-tour="generate"]').click()
        self.page.locator(".verify-summary").wait_for(timeout=STEP_TIMEOUT_MS)
        self.page.locator(".question").first.wait_for(timeout=STEP_TIMEOUT_MS)
        # 流式草稿题是负数 id（待验算）；done 事件校正后才有真 id 与验算徽章
        self.page.wait_for_function(
            "() => document.querySelectorAll('.question .tag-ok').length >= 4",
            timeout=STEP_TIMEOUT_MS,
        )
        summary = self.page.locator(".verify-summary").inner_text()
        assert "符号验算" in summary, f"汇总应注明符号验算：{summary}"
        badges = self.page.locator(".question .tag-ok").count()
        assert badges >= 4, f"交付题应带验算徽章，实际 {badges}"
        self.shot("t2-questions")
        self.ok(f"T2 出题 + 质检回环 → {badges} 题带徽章")

    def t3_grade_explain(self) -> None:
        # 第 4 题（答案 -1）：填 1 判错并触发讲解
        question = self.page.locator(".question").nth(3)
        question.get_by_placeholder("填写答案").fill(WRONG_ANSWER)
        question.get_by_role("button", name="提交判分").click()
        question.locator(".verdict-bad").wait_for(timeout=STEP_TIMEOUT_MS)
        self.page.locator(".explain-script").first.wait_for(timeout=STEP_TIMEOUT_MS)
        self.page.locator(".slider-viz").first.wait_for(timeout=STEP_TIMEOUT_MS)
        self.shot("t3-explain")
        self.ok("T3 答错判分 → 可视化讲解")

    def t4_ask(self) -> None:
        self.page.get_by_placeholder("例如：为什么这里要配方？").fill(ASK_QUERY)
        panel = self.page.locator(".panel-ask")
        panel.get_by_role("button", name="提问").click()
        answer = panel.locator(".answer")
        answer.wait_for(timeout=STEP_TIMEOUT_MS)
        assert answer.inner_text().strip(), "追问应有回答"
        assert panel.locator(".citations .cite").count() >= 1, "回答必须标注出处"
        self.shot("t4-ask")
        self.ok("T4 追问 → 带出处回答")

    def t5_review(self) -> None:
        question = self.page.locator(".question").nth(3)
        question.get_by_placeholder("填写答案").fill(REVIEW_ANSWER)
        question.get_by_role("button", name="AI 批改").click()
        question.locator(".review-panel-ok").wait_for(timeout=STEP_TIMEOUT_MS)
        assert question.locator(".review-step").count() >= 2, "批改应有分步清单"
        text = question.locator(".review-panel").inner_text()
        assert "分" in text, "批改面板应显示分数"
        self.shot("t5-review")
        self.ok("T5 AI 批改 → 流式评语 + 分步清单")

    def t6_revision(self) -> None:
        self.page.locator(".btn-edit").first.click()
        editor = self.page.locator(".card-editor")
        editor.wait_for(timeout=STEP_TIMEOUT_MS)
        title_input = editor.locator("input").first
        title_input.fill(NEW_CARD_TITLE)
        editor.get_by_role("button", name="保存修正").click()
        self.page.locator(".tag-edited").first.wait_for(timeout=STEP_TIMEOUT_MS)
        # 刷新后徽章仍在（留痕落库，不是会话内状态）
        self.page.reload()
        self.page.locator(".tag-edited").first.wait_for(timeout=STEP_TIMEOUT_MS)
        self.page.locator(".btn-edit").first.click()
        revisions = self.page.locator(".editor-revisions")
        revisions.wait_for(timeout=STEP_TIMEOUT_MS)
        revisions.locator("summary").click()  # 展开折叠块才能看到条目
        assert NEW_CARD_TITLE in revisions.inner_text(), "历史应含改后值"
        self.page.get_by_role("button", name="取消").click()
        self.shot("t6-revision")
        self.ok("T6 人工修正 → 留痕徽章刷新不丢 + 历史可查")

    def t7_event_replay(self) -> None:
        started = _api(self.port, "/api/ask", {"query": ASK_QUERY})
        run_id = started["run_id"]
        deadline = time.time() + STEP_TIMEOUT_MS / 1000
        events: list = []
        while time.time() < deadline:
            try:
                events = _api(self.port, f"/api/runs/{run_id}/events")["events"]
            except urllib.error.HTTPError:
                events = []
            if any(
                e["agent"] == "orchestrator" and e["phase"] == "done" for e in events
            ):
                break
            time.sleep(0.3)
        assert events, "落库事件不应为空"
        assert events[-1]["agent"] == "orchestrator", "末条应是编排器结束事件"
        self.ok(f"T7 事件落库回放 → {len(events)} 条")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一纸成课端到端冒烟")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--shots", type=Path, default=None, help="截图输出目录")
    parser.add_argument(
        "--server-python",
        default=sys.executable,
        help="起后端用的解释器（缺省与脚本同一个）",
    )
    args = parser.parse_args(argv)

    if not (WEB_DIST / "index.html").is_file():
        print("缺前端产物：先在 apps/web 执行 npm run build", file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="yizhi-e2e-", ignore_cleanup_errors=True) as tmp:
        env = {
            **os.environ,
            "SERVE_WEB": "1",
            "WEB_DIST": str(WEB_DIST),
            "APP_DATA_DIR": tmp,
            "APP_PORT": str(args.port),
            "APP_HOST": HOST,
        }
        server = subprocess.Popen(
            [
                args.server_python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                HOST,
                "--port",
                str(args.port),
            ],
            cwd=str(SERVER_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_health(args.port)
            with sync_playwright() as pw:
                launch_kwargs: dict = {"headless": True}
                try:
                    browser = pw.chromium.launch(**launch_kwargs)
                except Exception:
                    executable = _chromium_executable()
                    if not executable:
                        raise
                    browser = pw.chromium.launch(
                        headless=True, executable_path=executable
                    )
                page = browser.new_page(viewport=VIEWPORT)
                page.add_init_script(
                    f"localStorage.setItem({json.dumps(TOUR_DONE_KEY)}, '1')"
                )
                page.goto(f"http://{HOST}:{args.port}/")
                smoke = Smoke(page, args.port, args.shots)
                smoke.t1_upload()
                smoke.t2_generate()
                smoke.t3_grade_explain()
                smoke.t4_ask()
                smoke.t5_review()
                smoke.t6_revision()
                smoke.t7_event_replay()
                browser.close()
            print(f"全部通过：{len(smoke.passed)}/7")
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())