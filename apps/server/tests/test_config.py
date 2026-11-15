"""Provider 缺省映射：只补未显式设置的项，密钥不进代码。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

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


def test_mock_provider_is_always_ready(monkeypatch):
    monkeypatch.setattr(config, "VLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "VLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "VLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")

    assert config.provider_status() == {"vlm_ready": True, "llm_ready": True}


def test_real_provider_needs_key_url_and_model(monkeypatch):
    monkeypatch.setattr(config, "VLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "LLM_PROVIDER", "qwen")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen-plus")

    assert config.provider_status()["llm_ready"] is True

    monkeypatch.setattr(config, "LLM_MODEL", "")
    assert config.provider_status()["llm_ready"] is False

    monkeypatch.setattr(config, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(config, "LLM_BASE_URL", "  ")
    assert config.provider_status()["llm_ready"] is False

    monkeypatch.setattr(config, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert config.provider_status()["llm_ready"] is False


def test_unknown_provider_is_not_ready(monkeypatch):
    monkeypatch.setattr(config, "VLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "LLM_PROVIDER", "typo-provider")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")

    assert config.provider_status()["llm_ready"] is False


CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "app" / "config.py"


def test_app_data_dir_from_dotenv_takes_effect(tmp_path, monkeypatch):
    """APP_DATA_DIR 写在 .env 里也必须生效：.env 要在算 DATA_DIR 之前加载。"""
    monkeypatch.delenv("APP_DATA_DIR", raising=False)
    config_path = tmp_path / "apps" / "server" / "app" / "config.py"
    config_path.parent.mkdir(parents=True)
    shutil.copy(CONFIG_SOURCE, config_path)

    data_dir = tmp_path / "dotenv-data"
    (tmp_path / ".env").write_text(f"APP_DATA_DIR={data_dir}\n", encoding="utf-8")

    runner = tmp_path / "run_config.py"
    runner.write_text(
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('cfg', {str(config_path)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(module.DATA_DIR)\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key != "APP_DATA_DIR"}
    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(data_dir)
