# 第三方组件与许可说明

本项目源码以根目录 `LICENSE`（MIT）发布。桌面版二进制额外包含下列第三方组件；这些组件仍归其各自作者所有，并按各自许可分发。

## 需要特别注意：PyMuPDF

- 组件：PyMuPDF（`pymupdf`），用于 PDF 页面渲染兜底。
- 许可：**GNU Affero General Public License v3.0 或 Artifex 商业许可**（双许可）。
- 上游：<https://github.com/pymupdf/PyMuPDF>；AGPL-3.0 全文：<https://www.gnu.org/licenses/agpl-3.0.txt>。
- 本项目未购买 Artifex 商业许可，因此包含 PyMuPDF 的桌面版二进制按 AGPL-3.0 分发；对应源码可从本仓库的同一 tag/commit 获取（`apps/server`、`apps/desktop` 与 `requirements.txt` 即构建输入）。
- 如果不希望接受 AGPL 条款，可自行移除 `pymupdf` 依赖（PDF 文本直读仍可用，扫描件渲染兜底会失效），或向 Artifex 购买商业许可。

## 前端

| 组件 | 许可 | 上游 |
|---|---|---|
| React / React DOM | MIT | <https://github.com/facebook/react> |
| KaTeX | MIT | <https://github.com/KaTeX/KaTeX> |
| KaTeX 字体 | SIL Open Font License 1.1 | <https://github.com/KaTeX/KaTeX/tree/main/fonts> |

## 桌面壳与运行时

| 组件 | 许可 | 说明 |
|---|---|---|
| Electron | MIT | 包内 `LICENSE.electron.txt` |
| Chromium | BSD 等 | 包内 `LICENSES.chromium.html` |
| Python | PSF License | 包内 `python/LICENSE.txt` |
| FastAPI / Starlette / Uvicorn / Pydantic 等 | MIT / BSD / Apache-2.0 | 见 `site-packages/*.dist-info` |

## 演示素材

`apps/web/public/samples/` 下的板书图由 `scripts/make_samples.py` 生成；`sample-exam.pdf` 由仓库内 `data/samples/latex/sample-exam.tex` 自编。未使用真实学生资料或外部图库素材。

> 本文件随桌面版 Release 一并分发；若只使用 Web 模式，PyMuPDF 仍是 `requirements.txt` 的依赖，AGPL 说明同样适用。
