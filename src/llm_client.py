"""
LLM 客户端模块

多端点降级链：
  1. OpenClaw 19000 proxy  (modelroute 模型)
  2. OpenClaw 28789 gateway (openclaw 模型)
  3. 直连 API Key          (用户配置的 base_url + api_key)

参考 arxiv_agent LLMClient 设计。
"""

import logging
import time
from typing import Optional

import yaml

logger = logging.getLogger("fh_protocol_compare.llm")


class LLMClient:
    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self._endpoints = self._build_endpoints()
        self._current = 0  # 当前使用的端点索引
        self._consecutive_failures = 0
        self._FORBIDDEN_THRESHOLD = 2  # 连续 403 后降级

    def _load_config(self, config_path: Optional[str]) -> dict:
        if config_path:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        # 读取项目配置
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "config" / "settings.yml"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    def _build_endpoints(self) -> list[dict]:
        """构建降级链端点列表"""
        endpoints = []

        # 端点 1：OpenClaw 19000 proxy（优先）
        endpoints.append({
            "name": "19000_proxy",
            "base_url": "http://localhost:19000/v1",
            "model": "modelroute",        # placeholder，路由层自己认模型
            "api_key": "dummy",
            "header_auth": False,
        })

        # 端点 2：OpenClaw 28789 gateway
        endpoints.append({
            "name": "28789_gateway",
            "base_url": "http://localhost:28789/v1",
            "model": "openchat/oftary",
            "api_key": "dummy",
            "header_auth": False,
        })

        # 端点 3：直连 API Key（用户提供）
        api_key = self.config.get("llm", {}).get("api_key", "")
        base_url = self.config.get("llm", {}).get("base_url", "https://api.openai.com/v1")
        model = self.config.get("llm", {}).get("model", "gpt-3.5-turbo")
        if api_key:
            endpoints.append({
                "name": "api_key",
                "base_url": base_url,
                "model": model,
                "api_key": api_key,
                "header_auth": True,
            })

        return endpoints

    def _get_current_endpoint(self) -> dict:
        return self._endpoints[self._current]

    def _downgrade(self) -> bool:
        """尝试降级到下一个端点，返回是否还有可用端点"""
        if self._current < len(self._endpoints) - 1:
            self._current += 1
            self._consecutive_failures = 0
            ep = self._get_current_endpoint()
            logger.warning(f"LLM 降级至: {ep['name']} ({ep['base_url']})")
            return True
        logger.error("所有 LLM 端点均不可用")
        return False

    def _call(self, messages: list[dict], **kwargs) -> dict:
        """
        调用当前端点的 chat completion 接口。
        返回 OpenAI-compatible response dict。
        """
        import requests

        ep = self._get_current_endpoint()
        url = f"{ep['base_url']}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if ep["header_auth"] and ep.get("api_key"):
            headers["Authorization"] = f"Bearer {ep['api_key']}"

        payload = {
            "model": ep["model"],
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.3),
        }
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        resp = requests.post(url, headers=headers, json=payload, timeout=kwargs.get("timeout", 120))
        return resp

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 120,
    ) -> str:
        """
        发送对话请求，自动处理降级和重试。

        Args:
            messages:  [{"role": "system"|"user"|"assistant", "content": "..."}]
            temperature: 采样温度
            max_tokens: 最大返回 token 数
            timeout: 请求超时（秒）

        Returns:
            LLM 输出的文本内容

        Raises:
            RuntimeError: 所有端点均不可用
        """
        attempt = 0
        max_attempts = len(self._endpoints) * 2

        while attempt < max_attempts:
            attempt += 1
            ep = self._get_current_endpoint()
            logger.debug(f"[LLM] 调用 {ep['name']}，attempt={attempt}")

            try:
                resp = self._call(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    self._consecutive_failures = 0
                    return data["choices"][0]["message"]["content"].strip()

                elif resp.status_code == 403:
                    self._consecutive_failures += 1
                    logger.warning(f"[LLM] {ep['name']} 返回 403（{self._consecutive_failures}次）")
                    if self._consecutive_failures >= self._FORBIDDEN_THRESHOLD:
                        if not self._downgrade():
                            raise RuntimeError("所有 LLM 端点均已拒绝访问")
                    continue

                elif resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 30))
                    logger.warning(f"[LLM] {ep['name']} 返回 429，等待 {wait}s")
                    time.sleep(wait)
                    continue

                else:
                    logger.warning(f"[LLM] {ep['name']} 返回 {resp.status_code}: {resp.text[:200]}")
                    if not self._downgrade():
                        raise RuntimeError(f"LLM 请求失败: {resp.status_code}")
                    continue

            except Exception as e:
                logger.warning(f"[LLM] {ep['name']} 异常: {e}")
                if not self._downgrade():
                    raise RuntimeError(f"LLM 调用失败: {e}")
                continue

        raise RuntimeError("LLM 调用超出最大尝试次数")

    def chat_structured(
        self,
        messages: list[dict],
        schema: dict,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> dict:
        """
        带结构化输出的 chat 调用。
        通过 system prompt 要求 LLM 输出 JSON，
        解析后返回 dict（校验由调用方负责）。
        """
        import json

        schema_str = json.dumps(schema, ensure_ascii=False)
        prompt_extra = (
            f"\n\n请严格按以下 JSON Schema 输出，"
            f"不要输出任何其他内容：\n{schema_str}"
        )
        enhanced = list(messages)
        if enhanced and enhanced[0]["role"] == "system":
            enhanced[0]["content"] += prompt_extra
        else:
            enhanced.insert(0, {"role": "system", "content": prompt_extra})

        raw = self.chat(enhanced, temperature=temperature, max_tokens=max_tokens)
        # 提取 JSON 块
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group())
        return {"raw": raw}


# 懒加载单例
_llm_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
