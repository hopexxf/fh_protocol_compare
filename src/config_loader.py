"""
配置加载模块

从 config/settings.yml 读取配置，支持环境变量覆盖。
"""

import os
from pathlib import Path
from typing import Any

import yaml


class Config:
    _instance = None
    _data: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        """从 settings.yml 加载配置"""
        config_path = Path(__file__).resolve().parent.parent / "config" / "settings.yml"
        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def get(self, key: str, default: Any = None) -> Any:
        """
        读取配置值，支持点号路径，如 "llm.model"

        环境变量优先级高于配置文件：
          LLM_MODEL  -> llm.model
          LLM_BASE_URL -> llm.base_url
          LLM_API_KEY  -> llm.api_key
          LOG_DIR      -> paths.log_dir
        """
        # 环境变量覆盖
        env_map = {
            "llm.model":        "LLM_MODEL",
            "llm.base_url":     "LLM_BASE_URL",
            "llm.api_key":      "LLM_API_KEY",
            "paths.log_dir":    "LOG_DIR",
            "paths.output_dir": "OUTPUT_DIR",
        }
        env_key = env_map.get(key)
        if env_key and env_key in os.environ:
            return os.environ[env_key]

        # 路径解析
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def resolve_path(self, key: str) -> Path:
        """读取并解析为绝对路径（支持相对路径）"""
        val = self.get(key, "")
        if not val:
            return Path.cwd()
        p = Path(val).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def reload(self) -> None:
        """重新加载配置"""
        self._load()


def get_config() -> Config:
    return Config()


# ---------------------------------------------------------------------------
# Boilerplate 黑名单配置加载
# ---------------------------------------------------------------------------

_boilerplate_cache: dict = {}


def get_boilerplate_config() -> dict:
    """
    加载 boilerplate.yml 黑名单配置

    Returns:
        dict: 包含 title_blacklist / content_signals / toc_patterns / defaults 的字典
    """
    global _boilerplate_cache

    if _boilerplate_cache:
        return _boilerplate_cache

    bp_path = Path(__file__).resolve().parent.parent / "config" / "boilerplate.yml"
    if not bp_path.exists():
        return {}

    with open(bp_path, "r", encoding="utf-8") as f:
        _boilerplate_cache = yaml.safe_load(f) or {}

    return _boilerplate_cache


# ---------------------------------------------------------------------------
# 业务知识加载
# ---------------------------------------------------------------------------

_knowledge_cache: dict = {}


def get_knowledge() -> dict:
    """
    加载业务知识配置（knowledge.yml）
    
    Returns:
        dict: 包含 org_background / layer_responsibility / diff_patterns 的字典
    """
    global _knowledge_cache
    
    if _knowledge_cache:
        return _knowledge_cache
    
    # 从配置读取路径，默认 config/knowledge.yml
    config = get_config()
    knowledge_path_str = config.get("paths.knowledge_config", "config/knowledge.yml")
    knowledge_path = Path(__file__).resolve().parent.parent / knowledge_path_str
    
    if not knowledge_path.exists():
        # 不存在时返回空字典，analyzer 会降级为无知识注入
        return {}
    
    with open(knowledge_path, "r", encoding="utf-8") as f:
        _knowledge_cache = yaml.safe_load(f) or {}
    
    return _knowledge_cache
