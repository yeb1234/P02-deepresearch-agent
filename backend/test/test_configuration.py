"""Tests for Configuration model — default values, env var overrides, etc."""

import os
import pytest
from agent.configuration import (
    Configuration,
    ModelConfig,
    load_available_models_from_env,
    get_default_model_id,
    resolve_model_id,
)


class TestModelConfig:
    def test_valid_model_config(self):
        mc = ModelConfig(model_id="qwen-max", display_name="Qwen-Max")
        assert mc.model_id == "qwen-max"
        assert mc.display_name == "Qwen-Max"
        assert mc.icon == "Zap"
        assert mc.icon_color == "yellow-400"

    def test_custom_icon(self):
        mc = ModelConfig(
            model_id="test", display_name="Test", icon="Cpu", icon_color="purple-400"
        )
        assert mc.icon == "Cpu"
        assert mc.icon_color == "purple-400"

    def test_missing_model_id_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelConfig(display_name="NoId")


class TestLoadAvailableModelsFromEnv:
    def test_default_models_when_no_env(self, monkeypatch):
        monkeypatch.delenv("AVAILABLE_MODELS", raising=False)
        monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
        models = load_available_models_from_env()
        assert len(models) == 3
        assert models[0].model_id == "qwen-plus-latest"
        assert models[0].is_default is True
        assert models[-1].model_id == "deepseek-v4.1-flash"

    def test_load_from_env_json(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1","icon":"Zap","icon_color":"red-400"},'
            '{"model_id":"custom-2","display_name":"Custom 2","icon":"Cpu","icon_color":"blue-400"}]',
        )
        models = load_available_models_from_env()
        assert len(models) == 2
        assert models[0].model_id == "custom-1"
        assert models[1].model_id == "custom-2"

    def test_invalid_json_falls_back(self, monkeypatch):
        monkeypatch.setenv("AVAILABLE_MODELS", "not valid json")
        models = load_available_models_from_env()
        assert len(models) == 3  # fallback defaults


class TestGetDefaultModelId:
    def test_returns_first_by_default(self, monkeypatch):
        monkeypatch.delenv("AVAILABLE_MODELS", raising=False)
        monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
        default = get_default_model_id()
        assert default == "qwen-plus-latest"

    def test_explicit_default_env(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1"},'
            '{"model_id":"custom-2","display_name":"Custom 2"}]',
        )
        monkeypatch.setenv("DEFAULT_MODEL_ID", "custom-2")
        assert get_default_model_id() == "custom-2"

    def test_invalid_default_env_falls_back(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1"}]',
        )
        monkeypatch.setenv("DEFAULT_MODEL_ID", "not-in-list")
        assert get_default_model_id() == "custom-1"

    def test_is_default_flag_wins_over_order(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1"},'
            '{"model_id":"custom-2","display_name":"Custom 2","is_default":true}]',
        )
        monkeypatch.delenv("DEFAULT_MODEL_ID", raising=False)
        assert get_default_model_id() == "custom-2"


class TestResolveModelId:
    def test_accepts_allowlisted_model(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1","is_default":true},'
            '{"model_id":"custom-2","display_name":"Custom 2"}]',
        )
        assert resolve_model_id("custom-2") == "custom-2"

    def test_unknown_or_empty_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(
            "AVAILABLE_MODELS",
            '[{"model_id":"custom-1","display_name":"Custom 1","is_default":true}]',
        )
        assert resolve_model_id("attacker-model") == "custom-1"
        assert resolve_model_id("") == "custom-1"
        assert resolve_model_id(None) == "custom-1"


class TestConfigurationDefaults:
    def test_default_values(self):
        cfg = Configuration()
        assert cfg.number_of_initial_queries == 2
        assert cfg.max_research_loops == 2
        assert isinstance(cfg.query_generator_model, str)
        assert isinstance(cfg.reflection_model, str)
        assert isinstance(cfg.answer_model, str)
        assert len(cfg.available_models) > 0

    def test_available_models_is_list_of_model_config(self):
        cfg = Configuration()
        assert all(isinstance(m, ModelConfig) for m in cfg.available_models)


class TestFromRunnableConfig:
    def test_empty_config_returns_defaults(self):
        cfg = Configuration.from_runnable_config(None)
        assert cfg.number_of_initial_queries == 2
        assert cfg.max_research_loops == 2

    def test_configurable_overrides(self):
        cfg = Configuration.from_runnable_config(
            {"configurable": {"number_of_initial_queries": 5, "max_research_loops": 10}}
        )
        assert cfg.number_of_initial_queries == 5
        assert cfg.max_research_loops == 10

    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("NUMBER_OF_INITIAL_QUERIES", "8")
        cfg = Configuration.from_runnable_config(
            {"configurable": {"number_of_initial_queries": 3}}
        )
        assert cfg.number_of_initial_queries == 8

    def test_model_fields_from_configurable(self):
        cfg = Configuration.from_runnable_config(
            {
                "configurable": {
                    "query_generator_model": "model-a",
                    "reflection_model": "model-b",
                    "answer_model": "model-c",
                }
            }
        )
        assert cfg.query_generator_model == "model-a"
        assert cfg.reflection_model == "model-b"
        assert cfg.answer_model == "model-c"

    def test_partial_override(self):
        cfg = Configuration.from_runnable_config(
            {"configurable": {"max_research_loops": 7}}
        )
        assert cfg.max_research_loops == 7
        assert cfg.number_of_initial_queries == 2  # default

    def test_available_models_not_overridable_by_configurable(self):
        """available_models should always come from env, not configurable."""
        cfg = Configuration.from_runnable_config(
            {"configurable": {"available_models": "malicious"}}
        )
        assert isinstance(cfg.available_models, list)
        assert len(cfg.available_models) > 0
