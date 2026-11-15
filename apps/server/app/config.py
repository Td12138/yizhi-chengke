"""全局配置：一律环境变量驱动，缺省走可离线运行的最稳路径（mock）。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载：仅支持 KEY=VALUE，已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# 必须先加载 .env 再读 APP_DATA_DIR：否则写在 .env 里的数据目录不生效
_load_dotenv(PROJECT_ROOT / ".env")

# 桌面版由 Electron 用 APP_DATA_DIR 指到用户可写目录；缺省仍是仓库内 data/
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or str(PROJECT_ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "yizhi.db"

# ---- Provider（mock 保证无密钥也能跑通全链路）----
VLM_PROVIDER = os.environ.get("VLM_PROVIDER", "mock").strip().lower()
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock").strip().lower()

# 已知 Provider 的缺省地址与模型名：只补「环境变量没显式设置」的那几项，
# 密钥一律从 VLM_API_KEY / LLM_API_KEY 读，不在此处出现。
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "vlm_model": "qwen-vl-max",
        "llm_model": "qwen-plus",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "vlm_model": "glm-4v-plus",
        "llm_model": "glm-4-plus",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-chat",
    },
}


def provider_presets(provider: str) -> dict[str, str]:
    """按 provider 名取缺省配置；未知 provider 返回空字典。"""
    return dict(PROVIDER_PRESETS.get(provider.strip().lower(), {}))


def resolve_provider_value(
    provider: str,
    preset_key: str,
    env_key: str,
    environ: dict[str, str] | None = None,
) -> str:
    """显式环境变量 > provider 缺省 > 空串（空串由调用方决定怎么报错）。"""
    env = os.environ if environ is None else environ
    explicit = (env.get(env_key) or "").strip()
    if explicit:
        return explicit
    return provider_presets(provider).get(preset_key, "")


VLM_API_KEY = os.environ.get("VLM_API_KEY", "").strip()
VLM_BASE_URL = resolve_provider_value(VLM_PROVIDER, "base_url", "VLM_BASE_URL")
VLM_MODEL = resolve_provider_value(VLM_PROVIDER, "vlm_model", "VLM_MODEL")

LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE_URL = resolve_provider_value(LLM_PROVIDER, "base_url", "LLM_BASE_URL")
LLM_MODEL = resolve_provider_value(LLM_PROVIDER, "llm_model", "LLM_MODEL")

# ---- 编排约束 ----
VERIFY_MAX_RETRY = int(os.environ.get("VERIFY_MAX_RETRY", "2"))
QUESTION_MIN = 1
QUESTION_MAX = 12
DEFAULT_QUESTION_COUNT = 5
RAG_TOP_K = 3
# 生成接口一次最多接受多少张卡片：超出直接截断，避免一次 SQL 拼进上千个占位符
MAX_GENERATE_CARD_IDS = 200
# 逐条推送节奏（秒）：出题一条一题、卡片一张一条，给前端留出逐条出现的时间
DELTA_PACE_SECONDS = 0.04
# 批改评语切片推送节奏（秒）：mock 模拟逐字输出时用
CHUNK_PACE_SECONDS = 0.03

# ---- 服务 ----
HOST = os.environ.get("APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_PORT", "8710"))


def _env_flag(name: str, default: str = "false") -> bool:
    """环境变量开关：1/true/yes/on 为真，其余为假。"""
    return (os.environ.get(name, default) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---- 可选静态托管（桌面封装用；缺省 false，Web 开发模式行为不变）----
SERVE_WEB = _env_flag("SERVE_WEB")
# 前端产物目录：打包后由桌面外壳用 WEB_DIST 指到 resources/web
WEB_DIST = Path(os.environ.get("WEB_DIST", str(PROJECT_ROOT / "apps" / "web" / "dist")))
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if o.strip()
]

# ---- 上传约束 ----
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# 文档输入：PDF 常见字体子集缺 ToUnicode，解析时要走「文本检测 → 渲染兜底」双路径
DOC_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
# 后缀 → 输入类型；上传接口只认这张表，新增格式只改这里
KIND_BY_SUFFIX = {
    **{suffix: "image" for suffix in ALLOWED_SUFFIXES},
    **{suffix: "document" for suffix in DOC_SUFFIXES},
}
IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_DOC_BYTES = 20 * 1024 * 1024
# 归档文件的下载 MIME：/api/sources/{id}/image 也用来取 PDF / DOCX / 文本
SOURCE_MIME_BY_SUFFIX = {
    **IMAGE_MIME_BY_SUFFIX,
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
FALLBACK_MIME = "application/octet-stream"

# ---- 文档解析约束 ----
# PDF 文本层不可用时，最多渲染前 N 页交视觉模型（180 DPI）
MAX_DOC_PAGES = 3
# 单份文档最多取多少字符：防止整本书把上下文撑爆
MAX_DOC_CHARS = 20000


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _provider_ready(
    provider: str, api_key: str, base_url: str, model: str
) -> bool:
    """mock 恒就绪；真实模型必须 key / 地址 / 模型名三项齐全。

    只配了 key 但地址或模型是空串（常见于拼错 provider 名），请求一定失败，
    这里必须提前报「未就绪」，不能让前端以为可以点生成。
    """
    if provider == "mock":
        return True
    return bool(
        api_key.strip() and base_url.strip() and model.strip()
    )


def provider_status() -> dict:
    """provider 是否具备运行条件：mock 恒就绪，真实模型要求 key / 地址 / 模型齐全。"""
    return {
        "vlm_ready": _provider_ready(
            VLM_PROVIDER, VLM_API_KEY, VLM_BASE_URL, VLM_MODEL
        ),
        "llm_ready": _provider_ready(
            LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
        ),
    }
