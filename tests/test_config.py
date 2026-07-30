"""
test_config.py — Phase 1：配置加载测试

TC1-1：默认加载
TC1-2：环境变量覆盖
TC1-3：路径解析
"""

import os
import pytest
from pathlib import Path

from src.config_loader import get_config, Config


# ------------------------------------------------------------------
# TC1-1：默认加载
# ------------------------------------------------------------------

class TestConfigDefault:
    """验证 settings.yml 默认值正确读取"""

    def test_llm_model_default(self, project_root):
        cfg = get_config()
        assert cfg.get("llm.model") == "gpt-3.5-turbo"

    def test_paths_output_dir_default(self, project_root):
        cfg = get_config()
        assert cfg.get("paths.output_dir") == "versions"

    def test_alignment_threshold_default(self, project_root):
        cfg = get_config()
        assert cfg.get("alignment.similarity_threshold") == 0.3

    def test_diff_min_change_chars_default(self, project_root):
        cfg = get_config()
        assert cfg.get("diff.min_change_chars") == 30

    def test_llm_use_openclaw_default(self, project_root):
        cfg = get_config()
        assert cfg.get("llm.use_openclaw") is True

    def test_analysis_analyze_added_sections_default(self, project_root):
        cfg = get_config()
        assert cfg.get("analysis.analyze_added_sections") is True


# ------------------------------------------------------------------
# TC1-2：环境变量覆盖
# ------------------------------------------------------------------

class TestConfigEnvOverride:
    """验证环境变量覆盖配置文件"""

    def test_llm_model_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "gpt-4")
        Config._instance = None
        try:
            cfg = get_config()
            assert cfg.get("llm.model") == "gpt-4"
        finally:
            Config._instance = None

    def test_llm_api_key_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key-12345")
        Config._instance = None
        try:
            cfg = get_config()
            assert cfg.get("llm.api_key") == "sk-test-key-12345"
        finally:
            Config._instance = None

    def test_paths_log_dir_env_override(self, monkeypatch):
        monkeypatch.setenv("LOG_DIR", "custom_logs")
        Config._instance = None
        try:
            cfg = get_config()
            assert cfg.get("paths.log_dir") == "custom_logs"
        finally:
            Config._instance = None


# ------------------------------------------------------------------
# TC1-3：路径解析
# ------------------------------------------------------------------

class TestConfigPathResolution:
    """验证路径解析功能"""

    def test_resolve_path_returns_path_object(self, project_root):
        cfg = get_config()
        resolved = cfg.resolve_path("paths.output_dir")
        assert isinstance(resolved, Path)

    def test_resolve_path_relative_to_project(self, project_root):
        cfg = get_config()
        resolved = cfg.resolve_path("paths.output_dir")
        # 默认值为 "versions"，相对于项目根目录
        assert resolved.name == "versions"

    def test_resolve_path_absolute(self, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", "C:\\test\\output")
        Config._instance = None
        try:
            cfg = get_config()
            resolved = cfg.resolve_path("paths.output_dir")
            assert resolved.is_absolute()
        finally:
            Config._instance = None
