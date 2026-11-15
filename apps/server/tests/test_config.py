"""Provider 缺省映射：只补未显式设置的项，密钥不进代码。"""

from __future__ import annotations

from app import config


def test_qwen_presets():
    presets = config.provider_presets("qwen")
    assert presets["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert presets["vlm_model"] == "qwen-vl-max"
    assert presets["llm_model"] == "qwen-plus"


def test_glm_presets():
    presets = config.provider_presets("glm")
    assert presets["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert presets["vlm_model"] == "glm-4v-plus"
    assert presets["llm_model"] == "glm-4-plus"


def test_deepseek_is_text_only():
    presets = config.provider_presets("deepseek")
    assert presets["base_url"] == "https://api.deepseek.com/v1"
    assert presets["llm_model"] == "deepseek-chat"
    # 不给 deepseek 猜视觉模型：缺项就报错，不静默走错模型
    assert "vlm_model" not in presets


def test_provider_name_is_case_insensitive():
    assert config.provider_presets(" QWEN ") == config.provider_presets("qwen")


def test_unknown_provider_has_no_presets():
    assert config.provider_presets("mock") == {}
    assert config.provider_presets("local") == {}


def test_explicit_env_wins_over_preset():
    env = {"LLM_BASE_URL": "https://example.invalid/v1", "LLM_MODEL": "my-model"}
    assert (
        config.resolve_provider_value("qwen", "base_url", "LLM_BASE_URL", env)
        == "https://example.invalid/v1"
    )
    assert config.resolve_provider_value("qwen", "llm_model", "LLM_MODEL", env) == "my-model"


def test_preset_fills_missing_env():
    assert config.resolve_provider_value("qwen", "llm_model", "LLM_MODEL", {}) == "qwen-plus"
    assert config.resolve_provider_value("glm", "base_url", "LLM_BASE_URL", {}) == (
        "https://open.bigmodel.cn/api/paas/v4"
    )


def test_blank_env_falls_back_to_preset():
    assert (
        config.resolve_provider_value("qwen", "llm_model", "LLM_MODEL", {"LLM_MODEL": "  "})
        == "qwen-plus"
    )


def test_missing_everywhere_returns_empty():
    assert config.resolve_provider_value("mock", "base_url", "LLM_BASE_URL", {}) == ""


def test_presets_carry_no_secret():
    for name, preset in config.PROVIDER_PRESETS.items():
        assert preset, name
        assert not any("key" in field.lower() for field in preset), name
