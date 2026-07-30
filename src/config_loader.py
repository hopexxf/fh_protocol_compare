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
