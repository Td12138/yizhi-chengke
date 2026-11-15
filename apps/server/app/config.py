"""全局配置：一律环境变量驱动，缺省走可离线运行的最稳路径（mock）。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "yizhi.db"


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


_load_dotenv(PROJECT_ROOT / ".env")

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

# ---- 服务 ----
HOST = os.environ.get("APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_PORT", "8710"))
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if o.strip()
]

# ---- 上传约束 ----
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
