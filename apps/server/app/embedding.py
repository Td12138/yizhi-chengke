"""嵌入客户端：OpenAI 兼容 /embeddings 端点，供混合检索用。

缺省不启用（RETRIEVAL_MODE=legacy）；启用后任何网络/协议故障都由调用方
回退到 2-gram 检索——追问不能因为嵌入端点挂了而失败。

本地端点示例（llama.cpp / 任意 OpenAI 兼容服务）：
    EMBED_BASE_URL=http://127.0.0.1:8848/v1
    EMBED_MODEL=bge-m3-q8_0
"""

from __future__ import annotations

import math

import httpx

from . import config


class EmbeddingError(RuntimeError):
    """嵌入端点不可用或响应不合协议：调用方据此回退 legacy。"""


def _http_client() -> httpx.AsyncClient:
    """HTTP 客户端工厂：测试用 monkeypatch 注入 MockTransport，不走真实网络。"""
    return httpx.AsyncClient(timeout=float(config.EMBED_TIMEOUT_SECONDS))


def configured() -> bool:
    """hybrid 模式且端点两项齐全才算可用。"""
    return bool(
        config.RETRIEVAL_MODE == "hybrid"
        and config.EMBED_BASE_URL
        and config.EMBED_MODEL
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量嵌入：按 index 还原顺序，维度一致且全为有限数才接受。"""
    if not texts:
        return []
    if not config.EMBED_BASE_URL or not config.EMBED_MODEL:
        raise EmbeddingError("EMBED_BASE_URL / EMBED_MODEL 未配置")

    url = config.EMBED_BASE_URL.rstrip("/") + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if config.EMBED_API_KEY:
        headers["Authorization"] = f"Bearer {config.EMBED_API_KEY}"

    vectors: list[list[float]] = []
    try:
        async with _http_client() as client:
            for start in range(0, len(texts), config.EMBED_BATCH_SIZE):
                batch = texts[start : start + config.EMBED_BATCH_SIZE]
                resp = await client.post(
                    url,
                    json={"model": config.EMBED_MODEL, "input": batch},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json().get("data")
                if not isinstance(data, list) or len(data) != len(batch):
                    raise EmbeddingError("嵌入响应条数与请求不符")
                ordered = sorted(data, key=lambda item: item.get("index", 0))
                for item in ordered:
                    vector = item.get("embedding")
                    if not isinstance(vector, list) or not vector:
                        raise EmbeddingError("嵌入响应缺 embedding 数组")
                    numbers = [float(value) for value in vector]
                    if not all(math.isfinite(value) for value in numbers):
                        raise EmbeddingError("嵌入向量含非有限数")
                    vectors.append(numbers)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc

    dims = {len(vector) for vector in vectors}
    if len(dims) != 1:
        raise EmbeddingError("嵌入向量维度不一致")
    return vectors


def cosine(left: list[float], right: list[float]) -> float:
    """余弦相似度：零向量按 0 处理，不抛异常。"""
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)