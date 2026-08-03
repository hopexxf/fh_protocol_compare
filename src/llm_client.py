"""
LLM 客户端模块

调用方式：
  1. OpenClaw Gateway —— 端口与 Bearer Token 每次从 ~/.qclaw/openclaw.json 动态读取
     （Gateway 重启会漂移，故不写死端口），模型固定为 "openclaw"，非流式 POST
  2. 直连 API Key —— 用户提供 base_url + api_key（use_openclaw:false 时）

参考 arxiv_agent LLMClient 设计。
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("fh_protocol_compare.llm")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _scan_gateway_ports() -> Optional[int]:
    """主动扫描 QClaw 进程监听的 Gateway 端口，返回第一个可达的。

    当 openclaw.json 中记录的端口因重启漂移后，此函数兜底发现真实端口。
    QClaw Gateway 只监听 localhost，故只需扫本地端口列表。
    """
    import socket
    # QClaw 历史出现过的端口（从实测记录整理，含当前活跃端口）
    KNOWN_PORTS = [60760, 60772, 61791, 53311, 51900, 51901, 51902,
                   19000, 50000, 50001, 50002, 50003]
    for port in KNOWN_PORTS:
        try:
            with socket.create_connection(("localhost", port), timeout=0.5):
                logger.debug(f"[Gateway Scan] port {port} is reachable")
                return port
        except (OSError, socket.timeout):
            pass
    return None


def _get_gateway_port() -> int:
    """从 openclaw.json 动态读取当前 Gateway 监听端口，并主动验证可用性。

    Gateway 每次重启会从 openclaw.json 重新读取 gateway.port，端口会漂移
    （实测曾从 61791 → 53311 → 51900 → 60760 → 60772）。静态写在
    settings.yml 的端口会失效，因此每次调用都应从 openclaw.json 取实时值。
    若配置的端口实际不可达，则主动扫描兜底。
    """
    configured_port = None
    # 1) 优先读 openclaw.json 配置端口
    try:
        cfg_path = Path.home() / ".qclaw" / "openclaw.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            port = cfg.get("gateway", {}).get("port")
            if isinstance(port, int) and 1 <= port <= 65535:
                configured_port = port
    except Exception:
        pass
    # 2) 回退到 settings.yml
    try:
        from src.config_loader import get_config
        port = get_config().get("llm", {}).get("gateway_port")
        if isinstance(port, int) and 1 <= port <= 65535:
            configured_port = port
    except Exception:
        pass

    # 3) 验证配置的端口是否实际可达
    if configured_port:
        import socket
        try:
            with socket.create_connection(("localhost", configured_port), timeout=0.5):
                logger.debug(f"[Gateway Port] configured {configured_port} is reachable")
                return configured_port
        except (OSError, socket.timeout):
            logger.info(f"[Gateway Port] configured {configured_port} unreachable, scanning...")

    # 4) 扫描兜底
    discovered = _scan_gateway_ports()
    if discovered:
        logger.info(f"[Gateway Port] discovered active port: {discovered}")
        return discovered

    # 5) 最后兜底
    fallback = configured_port or 61791
    logger.warning(f"[Gateway Port] scan failed, using configured/fallback port: {fallback}")
    return fallback


def _load_gateway_token() -> Optional[str]:
    """从 openclaw.json 读取 gateway Bearer token"""
    cfg_path = Path.home() / ".qclaw" / "openclaw.json"
    if not cfg_path.exists():
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("gateway", {}).get("auth", {}).get("token", "")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self._endpoints = self._build_endpoints()
        self._current = 0
        self._consecutive_failures = 0
        self._FORBIDDEN_THRESHOLD = 2
        self._session_label: Optional[str] = None  # 当前会话来源标识（用于清理）

    def set_session_label(self, label: str) -> None:
        """设置会话来源标识，LLM 调用时会作为 user 字段传入，便于定位和清理。"""
        self._session_label = label

    def cleanup_sessions(self) -> bool:
        """
        清理 OpenClaw Gateway 中由本项目创建的所有会话。
        调用 openclaw sessions cleanup --enforce 应用维护策略。
        返回 True 表示清理命令执行成功（不代表有会话被删除）。
        """
        import subprocess, shutil
        oc = shutil.which("openclaw")
        if not oc:
            logger.warning("[LLM] openclaw CLI 未找到，跳过会话清理")
            return False
        try:
            result = subprocess.run(
                [oc, "sessions", "cleanup", "--enforce"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("[LLM] 会话清理完成")
                return True
            else:
                logger.warning(f"[LLM] 会话清理失败: {result.stderr.strip()}")
                return False
        except Exception as e:
            logger.warning(f"[LLM] 会话清理异常: {e}")
            return False

    def _load_config(self, config_path: Optional[str]) -> dict:
        if config_path:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        p = Path(__file__).resolve().parent.parent / "config" / "settings.yml"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    def _build_endpoints(self) -> list[dict]:
        endpoints = []

        # 端点 1：OpenClaw Gateway（从 openclaw.json 读 Bearer token）
        gw_token = _load_gateway_token()
        if gw_token:
            # 优先用 openclaw.json 实时端口，回退 settings.yml 的 gateway_port
            gw_port = _get_gateway_port()
            endpoints.append({
                "name": "gateway",
                "base_url": f"http://localhost:{gw_port}/v1",
                "model": "openclaw",
                "api_key": gw_token,
                "header_auth": True,
            })

        # 端点 2：直连 API Key（用户提供）
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

        if not endpoints:
            raise RuntimeError("未配置任何 LLM 端点，请设置 llm.api_key 或确保 OpenClaw Gateway 可用")
        return endpoints

    def _get_current_endpoint(self) -> dict:
        return self._endpoints[self._current]

    def _downgrade(self) -> bool:
        if self._current < len(self._endpoints) - 1:
            self._current += 1
            self._consecutive_failures = 0
            ep = self._get_current_endpoint()
            logger.warning(f"LLM 降级至: {ep['name']} ({ep['base_url']})")
            return True
        logger.error("所有 LLM 端点均不可用")
        return False

    def _call(self, messages: list[dict], stream: bool = True, **kwargs) -> dict:
        """
        调用当前端点。
        - stream=True（默认）：使用流式 SSE 读取，返回 {"text": "...", "done": True}
        - stream=False：同步 JSON 响应，返回 {"text": "...", "done": True}
        返回值统一格式，方便调用方处理。
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
            "stream": stream,
        }
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if self._session_label:
            payload["user"] = self._session_label

        timeout = kwargs.get("timeout", 120)
        resp = requests.post(url, headers=headers, json=payload, stream=stream, timeout=timeout)

        if not stream:
            if resp.status_code != 200:
                return {"error": resp.status_code, "text": resp.text[:200], "done": True}
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return {"text": text, "done": True}

        # 流式：手动解析 SSE
        return self._parse_sse_stream(resp)

    def _parse_sse_stream(self, resp) -> dict:
        """
        手动解析 SSE 流式响应，累积 delta.content 后返回完整文本。
        """
        content_parts = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
            except json.JSONDecodeError:
                continue
        return {"text": "".join(content_parts), "done": True}

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

            # 判断端点是否支持流式
            is_gateway = ep["name"] == "gateway"
            use_stream = is_gateway  # gateway 只支持流式

            try:
                result = self._call(
                    messages,
                    stream=use_stream,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )

                if "error" in result:
                    code = result["error"]
                    logger.warning(f"[LLM] {ep['name']} 返回 {code}: {result['text']}")
                    if not self._downgrade():
                        raise RuntimeError(f"LLM 请求失败: {code}")
                    continue

                self._consecutive_failures = 0
                return result["text"]

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
        通过 system prompt 要求 LLM 输出 JSON，解析后返回 dict。
        """
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
